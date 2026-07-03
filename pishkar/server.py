"""FastAPI + WebSocket server — the single stable entrypoint.

`python -m pishkar.server` is the only command. The OS-native daemon
(LaunchAgent / systemd / Task Scheduler) is a thin wrapper around the
same command — no second entrypoint exists.

The WebSocket endpoint speaks the typed event protocol from
`core/events.py`. On connect, an optional `last_event_id` query param
triggers an event replay from the SQLite log so a reconnecting client
(laptop sleep, network blip) does not lose anything emitted while it
was away. After replay, the socket is attached to the gateway as a
`WebSocketChannel` and live events stream through.

The agent handler is injected at app construction (`create_app`) so
tests can substitute a deterministic stub and `__main__` can wire the
real LiteLLM-backed loop.
"""

import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import uuid4

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from starlette.websockets import WebSocketState

from pishkar.channels.ws import WebSocketChannel
from pishkar.channels.ws import WebSocketDisconnect as ChannelWebSocketDisconnect
from pishkar.core.events import (
    ContentBlockDelta,
    Event,
    SessionChanged,
    TextDelta,
    TurnEnd,
    TurnStart,
    UserMessage,
)
from pishkar.core.messages import InboundMessage, OutboundMessage, TrustLevel
from pishkar.gateway.approval_router import ApprovalRouter
from pishkar.gateway.gateway import Gateway, Handler
from pishkar.gateway.hooks import HookManager
from pishkar.gateway.user_registry import UserChannelRegistry
from pishkar.observability.langfuse_sink import LangFuseSink
from pishkar.observability.phoenix_sink import PhoenixSink
from pishkar.observability.sqlite_sink import SqliteSink
from pishkar.tools.approval_gate import ApprovalDecision
from pishkar.workspace.store import SessionStore

logger = logging.getLogger(__name__)

HandlerFactory = Callable[[], Handler]


class ChannelRunner(Protocol):
    """Lifecycle hook for a channel that owns its own transport (e.g.
    a Telegram bot polling loop). The lifespan starts each runner after
    the gateway is ready and stops them in reverse on shutdown."""

    async def start(self) -> None: ...
    async def stop(self) -> None: ...


ChannelRunnerFactory = Callable[
    [Gateway, ApprovalRouter | None, UserChannelRegistry], list[ChannelRunner]
]


def create_app(
    *,
    store: SessionStore,
    handler: Handler,
    hooks: HookManager | None = None,
    recovery_target: set[str] | None = None,
    approval_router: ApprovalRouter | None = None,
    channel_runner_factory: ChannelRunnerFactory | None = None,
    user_registry: UserChannelRegistry | None = None,
    tool_registry: Any = None,
    model_selector: Any = None,
    user_id: str | None = None,
    transcriber: Any = None,
    webhook_config_path: Path | None = None,
    memory_index: Any = None,
) -> FastAPI:
    hooks = hooks or HookManager()
    sink = SqliteSink(store)
    sink.attach(hooks)
    user_registry = user_registry or UserChannelRegistry()

    gateway = Gateway(
        store=store, handler=_wrap_handler(handler, sink, store), hooks=hooks
    )
    runners: list[ChannelRunner] = (
        channel_runner_factory(gateway, approval_router, user_registry)
        if channel_runner_factory is not None
        else []
    )
    mcp_clients: list[Any] = []

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await store.open()
        if memory_index is not None:
            await memory_index.ensure_schema()
        if recovery_target is not None:
            from pishkar.runtime import recover_on_startup

            report = await recover_on_startup(store)
            recovery_target.update(report["interrupted_sessions"])
        if model_selector is not None and user_id:
            saved = await store.get_pref(user_id, "model")
            if saved and not model_selector.set_model(saved):
                logger.info(
                    "saved model %r is no longer in the catalog; "
                    "keeping default %r",
                    saved, model_selector.default(),
                )
        if tool_registry is not None:
            from pishkar.tools.mcp_config import connect_servers, load_config

            servers = load_config()
            if servers:
                mcp_clients.extend(
                    await connect_servers(servers, tool_registry)
                )
        await gateway.start()
        for runner in runners:
            await runner.start()
        try:
            yield
        finally:
            for runner in reversed(runners):
                with contextlib.suppress(Exception):
                    await runner.stop()
            await gateway.stop()
            if mcp_clients:
                from pishkar.tools.mcp_config import disconnect_servers

                with contextlib.suppress(Exception):
                    await disconnect_servers(mcp_clients)
            await store.close()

    app = FastAPI(lifespan=lifespan)
    app.state.store = store
    app.state.gateway = gateway
    app.state.hooks = hooks
    app.state.user_registry = user_registry

    if webhook_config_path is not None:
        from pishkar.triggers.webhook import build_webhook_router

        app.include_router(
            build_webhook_router(gateway.submit, webhook_config_path)
        )

    @app.get("/sessions/latest/{user_id}")
    async def _latest_session(user_id: str) -> dict[str, str | None]:
        return {"session_id": await store.latest_session_for_user(user_id)}

    @app.get("/sessions/{user_id}")
    async def _list_sessions(user_id: str) -> dict[str, Any]:
        rows = await store.recent_sessions_for_user(user_id)
        return {"sessions": rows}

    @app.post("/voice/{user_id}/{session_id}")
    async def _voice_in(
        user_id: str,
        session_id: str,
        audio: UploadFile = File(...),  # noqa: B008  # FastAPI idiom
        message_id: str | None = Form(default=None),
    ) -> dict[str, str]:
        """Browser-side voice input. The Web UI captures audio via
        MediaRecorder, POSTs the blob here, and we transcribe it through
        the configured Transcriber and dispatch as a normal text turn."""
        if transcriber is None:
            raise HTTPException(
                status_code=503, detail="Voice input is not configured."
            )
        data = await audio.read()
        if not data:
            raise HTTPException(status_code=400, detail="Empty audio upload.")
        try:
            text = await transcriber.transcribe(
                data, mime=audio.content_type or "audio/webm"
            )
        except Exception as exc:
            logger.exception("voice transcription failed for user %s", user_id)
            raise HTTPException(
                status_code=502, detail="Transcription failed."
            ) from exc
        text = text.strip()
        if not text:
            raise HTTPException(
                status_code=400, detail="Empty transcript."
            )
        kwargs: dict[str, Any] = {
            "user_id": user_id,
            "session_id": session_id,
            "channel": "web",
            "content": text,
        }
        if message_id:
            kwargs["message_id"] = message_id
        await gateway.submit(InboundMessage(**kwargs))
        return {"transcript": text, "message_id": message_id or ""}

    @app.post("/sessions/new/{user_id}")
    async def _new_session(user_id: str, source: str = "web") -> dict[str, str]:
        session = await store.create_session(user_id)
        await user_registry.broadcast(
            user_id,
            SessionChanged(
                session_id=session.session_id,
                user_id=user_id,
                source_channel=source,
            ),
        )
        return {"session_id": session.session_id}

    @app.websocket("/ws/{user_id}/{session_id}")
    async def _ws(
        socket: WebSocket,
        user_id: str,
        session_id: str,
        last_event_id: str | None = Query(default=None),
    ) -> None:
        await socket.accept()
        if not await _replay(socket, store, session_id, last_event_id):
            return  # client went away mid-replay; nothing more to do
        channel = WebSocketChannel(
            _StarletteSocketAdapter(socket),
            user_id=user_id,
            session_id=session_id,
            disconnect_exc=ChannelWebSocketDisconnect,
            control_handler=_make_control_handler(approval_router, session_id),
        )
        gateway.register_channel(session_id, channel)
        await user_registry.register(user_id, session_id, channel.send_event)
        if approval_router is not None:
            approval_router.bind(session_id, channel.send_event, channel="ws")
        try:
            async for msg in channel.inbound():
                await gateway.submit(msg)
        except WebSocketDisconnect:
            pass
        finally:
            if approval_router is not None:
                approval_router.unbind(session_id, channel="ws")
            await user_registry.unregister(user_id, channel.send_event)
            gateway.detach_channel(session_id, channel)
            await channel.close()

    return app


def _make_control_handler(
    approval_router: ApprovalRouter | None, session_id: str
) -> Callable[[dict[str, Any]], Any]:
    async def handle(payload: dict[str, Any]) -> None:
        if approval_router is None:
            return
        if payload.get("type") != "approval_response":
            return
        request_id = payload.get("request_id")
        decision_raw = payload.get("decision")
        if not isinstance(request_id, str) or not isinstance(decision_raw, str):
            return
        try:
            decision = ApprovalDecision(decision_raw)
        except ValueError:
            return
        approval_router.resolve(session_id, request_id, decision)

    return handle


def _wrap_handler(handler: Handler, sink: SqliteSink, store: SessionStore) -> Handler:
    """Echo the user's message as an event, then tee every assistant
    event through the always-on SQLite log so replay rebuilds the full
    chat (user side included).

    Also accumulates assistant text deltas during the turn and records
    one outbound `messages` row at TurnEnd. That gives `messages` a
    complete user/assistant transcript per session, which the runtime
    uses to hydrate history on cold start.

    A handler exception (e.g. provider 429, network blip) is converted
    into a `TurnEnd(stop_reason="error")` so the UI sees the turn close
    and the gateway worker stays alive for the next message.
    """

    def wrapped(msg: InboundMessage) -> AsyncIterator[Event]:
        async def gen() -> AsyncIterator[Event]:
            user_event = UserMessage(
                session_id=msg.session_id,
                message_id=msg.message_id,
                content=msg.content,
                channel=msg.channel,
            )
            await sink.write_event(user_event)
            yield user_event
            text_buf: list[str] = []
            try:
                async for event in handler(msg):
                    await sink.write_event(event)
                    if isinstance(event, ContentBlockDelta) and isinstance(
                        event.delta, TextDelta
                    ):
                        text_buf.append(event.delta.text)
                    elif isinstance(event, TurnEnd):
                        text = "".join(text_buf).strip()
                        text_buf.clear()
                        if text:
                            with contextlib.suppress(Exception):
                                await store.record_outbound(
                                    OutboundMessage(
                                        user_id=msg.user_id,
                                        session_id=msg.session_id,
                                        channel=msg.channel,
                                        content=text,
                                    )
                                )
                    yield event
            except Exception:
                logger.exception("handler failed for session %s", msg.session_id)
                err_event = TurnEnd(
                    turn_id="",
                    session_id=msg.session_id,
                    stop_reason="error",
                )
                await sink.write_event(err_event)
                yield err_event

        return gen()

    return wrapped


async def _replay(
    socket: WebSocket,
    store: SessionStore,
    session_id: str,
    last_event_id: str | None,
) -> bool:
    """Stream past events to a freshly-connected socket.

    Returns False if the client disconnected mid-replay (StrictMode
    double-mount, page refresh, sleep wake) so the caller can bail out
    cleanly instead of trying to attach a dead channel.
    """
    rows = await store.events_after(session_id, after_event_id=last_event_id)
    for row in rows:
        try:
            await socket.send_text(row["payload_json"])
        except WebSocketDisconnect:
            return False
    return True


class _StarletteSocketAdapter:
    """Bridges Starlette's WebSocket to the duck-typed shape that
    `WebSocketChannel` expects (async receive_text/send_text/close)."""

    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws

    async def receive_text(self) -> str:
        try:
            return await self._ws.receive_text()
        except WebSocketDisconnect as e:
            raise ChannelWebSocketDisconnect from e

    async def send_text(self, data: str) -> None:
        await self._ws.send_text(data)

    async def close(self) -> None:
        if self._ws.client_state != WebSocketState.DISCONNECTED:
            await self._ws.close()


# ---- entrypoint -----------------------------------------------------------


def _default_handler(msg: InboundMessage) -> AsyncIterator[Event]:
    """Placeholder handler — echoes a single TurnEnd. Replaced when the
    LiteLLM-backed `run_turn` wiring lands. Kept so `python -m pishkar.server`
    boots cleanly during day-one development."""

    async def gen() -> AsyncIterator[Event]:
        turn_id = str(uuid4())
        yield TurnStart(turn_id=turn_id, session_id=msg.session_id, turn_index=0)
        yield TurnEnd(turn_id=turn_id, session_id=msg.session_id, stop_reason="end_turn")

    return gen()


def main() -> None:
    import os

    import uvicorn
    from dotenv import load_dotenv

    # Load `.env` from cwd (and parents) so `python -m pishkar.server`
    # picks up provider keys without needing them exported in the shell.
    load_dotenv()

    from pishkar.runtime import PROVIDER_KEYS

    db_path = Path.home() / ".pishkar" / "sessions.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = SessionStore(db_path)

    has_key = any(os.environ.get(k) for k, _, _ in PROVIDER_KEYS)
    interrupted: set[str] = set()
    approval_router = ApprovalRouter()
    hooks = HookManager()
    _attach_trace_sink(hooks)
    model_selector = None
    tool_registry = None
    # Filled after `create_app` builds the gateway; the budget alert
    # closure reads it lazily so alerts can submit synthetic messages.
    gateway_holder: list[Gateway] = []
    if has_key:
        from pishkar.runtime import (
            ModelSelector,
            _default_registry,
            build_default_provider,
            build_handler,
        )
        from pishkar.workspace.loader import WorkspaceLoader

        loader = WorkspaceLoader(base_dir=db_path.parent)
        loader.ensure_starter(os.environ.get("PISHKAR_USER", "user"))
        provider, model = build_default_provider()
        daily_budget = _read_daily_budget()
        if daily_budget:
            from pishkar.providers.budgeted import BudgetedProvider

            provider = BudgetedProvider(
                provider,
                store,
                daily_budget,
                model_label=model,
                alert_fn=_make_budget_alert(store, gateway_holder),
            )
        model_selector = ModelSelector(default=model)
        # Build the registry up front so MCP tools (connected during the
        # lifespan startup) land in the same instance the handler reads.
        tool_registry = _default_registry()
        handler = build_handler(
            provider=provider,
            model=model_selector,
            registry=tool_registry,
            hooks=hooks,
            workspace_loader=loader,
            interrupted_sessions=interrupted,
            approval_router=approval_router,
            store=store,
        )
        _attach_reflector(hooks=hooks, provider=provider, model=model,
                          store=store, loader=loader)
    else:
        handler = _default_handler  # echo stub keeps the server bootable offline

    user_id = os.environ.get("PISHKAR_USER", "user")
    memory_index = _build_memory_index(store, hooks)
    transcriber = _build_transcriber()
    app = create_app(
        store=store,
        handler=handler,
        hooks=hooks,
        recovery_target=interrupted,
        approval_router=approval_router,
        tool_registry=tool_registry,
        model_selector=model_selector,
        user_id=user_id,
        transcriber=transcriber,
        webhook_config_path=db_path.parent / "webhooks.json",
        memory_index=memory_index,
        channel_runner_factory=_telegram_factory_from_env(
            user_id=user_id,
            store=store,
            model_selector=model_selector,
            transcriber=transcriber,
            synthesizer=_build_synthesizer(),
        ),
    )
    gateway_holder.append(app.state.gateway)
    uvicorn.run(app, host="127.0.0.1", port=8765)


def _read_daily_budget() -> int | None:
    """Parse `PISHKAR_DAILY_TOKEN_BUDGET` (tokens/day/user). None = no cap."""
    import os

    raw = os.environ.get("PISHKAR_DAILY_TOKEN_BUDGET")
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "PISHKAR_DAILY_TOKEN_BUDGET=%r is not an integer; budget disabled", raw
        )
        return None
    if value <= 0:
        logger.warning(
            "PISHKAR_DAILY_TOKEN_BUDGET=%r must be positive; budget disabled", raw
        )
        return None
    return value


def _make_budget_alert(
    store: SessionStore, gateway_holder: list[Gateway]
) -> Callable[[str, int, int, float], Any]:
    """Build the `BudgetedProvider.alert_fn` that turns a crossed spend
    threshold into a synthetic inbound message ("budget at 80% today…"),
    so the agent tells the user through whatever channel is attached."""

    async def alert(user_id: str, spent: int, budget: int, threshold: float) -> None:
        if not gateway_holder:
            return
        gateway = gateway_holder[0]
        session_id = await store.latest_session_for_user(user_id)
        if session_id is None:
            session_id = (await store.create_session(user_id)).session_id
        pct = round(threshold * 100)
        await gateway.submit(
            InboundMessage(
                user_id=user_id,
                session_id=session_id,
                channel="budget",
                content=(
                    f"System notice: today's token spend for {user_id} is "
                    f"{spent:,} of the {budget:,} daily budget — the {pct}% "
                    f"alert threshold was crossed. Briefly inform the user, "
                    f"and suggest switching to a cheaper model if that makes "
                    f"sense."
                ),
                metadata={
                    "budget_alert": {
                        "spent": spent,
                        "budget": budget,
                        "threshold": threshold,
                    }
                },
            )
        )

    return alert


def _attach_trace_sink(hooks: HookManager) -> None:
    """Wire the configured LLM-trace backend into the Hooks layer.

    `PISHKAR_TRACE_BACKEND` selects between `phoenix` (default; Pi 5 /
    SBC friendly), `langfuse` (richer dashboards; needs more RAM), and
    `none`. SDK imports happen lazily inside the build factories, so a
    missing optional dep just disables tracing instead of crashing the
    server.
    """
    import os

    backend = os.environ.get("PISHKAR_TRACE_BACKEND", "phoenix").lower()
    if backend == "none":
        return
    if backend == "phoenix":
        try:
            from pishkar.observability.phoenix_sink import build_phoenix_tracer

            tracer = build_phoenix_tracer(
                endpoint=os.environ.get(
                    "PHOENIX_ENDPOINT", "http://localhost:6006/v1/traces"
                ),
                project_name=os.environ.get("PHOENIX_PROJECT", "pishkar"),
            )
        except ImportError:
            logger.warning(
                "PISHKAR_TRACE_BACKEND=phoenix but `arize-phoenix-otel` is not "
                "installed; LLM tracing disabled."
            )
            return
        PhoenixSink(tracer).attach(hooks)
        return
    if backend == "langfuse":
        public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
        secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
        if not public_key or not secret_key:
            logger.warning(
                "PISHKAR_TRACE_BACKEND=langfuse but LANGFUSE_PUBLIC_KEY / "
                "LANGFUSE_SECRET_KEY are not set; LLM tracing disabled."
            )
            return
        try:
            from pishkar.observability.langfuse_sink import build_langfuse_client

            client = build_langfuse_client(
                public_key=public_key,
                secret_key=secret_key,
                host=os.environ.get("LANGFUSE_HOST", "http://localhost:3000"),
            )
        except ImportError:
            logger.warning(
                "PISHKAR_TRACE_BACKEND=langfuse but `langfuse` is not "
                "installed; LLM tracing disabled."
            )
            return
        LangFuseSink(client).attach(hooks)
        return
    logger.warning(
        "Unknown PISHKAR_TRACE_BACKEND=%r (expected 'phoenix', 'langfuse', "
        "or 'none'); LLM tracing disabled.",
        backend,
    )


def _attach_reflector(
    *,
    hooks: HookManager,
    provider: Any,
    model: str,
    store: SessionStore,
    loader: Any,
) -> None:
    """Subscribe the Reflector to `on_turn_complete` if enabled.

    Off by default (extraction is an extra LLM call per N turns). Set
    `PISHKAR_REFLECTOR_ENABLED=1` to opt in. `PISHKAR_REFLECTOR_EVERY_N`
    overrides the default trigger cadence (10 successful turns)."""
    import os

    if os.environ.get("PISHKAR_REFLECTOR_ENABLED", "").lower() not in {
        "1", "true", "yes", "on",
    }:
        return
    raw = os.environ.get("PISHKAR_REFLECTOR_EVERY_N")
    every_n = 10
    if raw:
        try:
            every_n = max(1, int(raw))
        except ValueError:
            logger.warning(
                "PISHKAR_REFLECTOR_EVERY_N=%r not an int; using default 10", raw
            )
    from pishkar.observability.reflector import Reflector

    Reflector(
        provider=provider,
        model=model,
        store=store,
        loader=loader,
        every_n_turns=every_n,
    ).attach(hooks)
    logger.info("reflector enabled (every %d turns)", every_n)


def _build_memory_index(store: SessionStore, hooks: HookManager) -> Any:
    """Construct the semantic-memory index if an embedding model is set.

    `PISHKAR_EMBEDDING_MODEL` (any litellm embedding model) enables it:
    new messages are embedded after each completed turn, and the
    `search_memory` tool starts returning real results. Unset = the tool
    explains how to enable itself and nothing is embedded.
    """
    import os

    model = os.environ.get("PISHKAR_EMBEDDING_MODEL")
    if not model:
        return None
    from pishkar.tools import memory as memory_tool
    from pishkar.workspace.memory import LiteLLMEmbedder, MemoryIndex

    index = MemoryIndex(store, LiteLLMEmbedder(model))
    index.attach(hooks)
    memory_tool.configure(index)
    logger.info("semantic memory enabled (embedding model %s)", model)
    return index


def _build_transcriber() -> Any:
    """Construct an STT engine if voice is enabled, else None."""
    try:
        from pishkar.voice.stt import build_transcriber_from_env
    except ImportError:
        return None
    return build_transcriber_from_env()


def _build_synthesizer() -> Any:
    """Construct a TTS engine if voice replies are enabled, else None."""
    try:
        from pishkar.voice.tts import build_synthesizer_from_env
    except ImportError:
        return None
    return build_synthesizer_from_env()


def _telegram_factory_from_env(
    *,
    user_id: str,
    store: SessionStore | None = None,
    model_selector: Any = None,
    transcriber: Any = None,
    synthesizer: Any = None,
) -> ChannelRunnerFactory | None:
    """Return a channel-runner factory if `TELEGRAM_BOT_TOKEN` is set,
    otherwise None. The runner is constructed lazily inside `create_app`
    so it can take the gateway built there."""
    import os

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    owner_raw = os.environ.get("TELEGRAM_OWNER_ID")
    if not token or not owner_raw:
        return None
    try:
        owner_id = int(owner_raw)
    except ValueError:
        logger.warning("TELEGRAM_OWNER_ID must be an integer; got %r", owner_raw)
        return None

    trust_raw = os.environ.get("PISHKAR_TELEGRAM_TRUST", "full").lower()
    if trust_raw not in {"full", "limited", "untrusted"}:
        logger.warning(
            "PISHKAR_TELEGRAM_TRUST=%r is not one of full/limited/untrusted; "
            "using 'full'",
            trust_raw,
        )
        trust_raw = "full"
    trust_level = cast(TrustLevel, trust_raw)

    def factory(
        gateway: Gateway,
        approval_router: ApprovalRouter | None,
        user_registry: UserChannelRegistry,
    ) -> list[ChannelRunner]:
        from pishkar.channels.telegram import TelegramBotRunner

        return [
            TelegramBotRunner(
                token=token,
                owner_id=owner_id,
                user_id=user_id,
                gateway=gateway,
                approval_router=approval_router,
                store=store,
                user_registry=user_registry,
                model_selector=model_selector,
                transcriber=transcriber,
                synthesizer=synthesizer,
                trust_level=trust_level,
            )
        ]

    return factory


if __name__ == "__main__":
    main()


__all__ = [
    "ChannelRunner",
    "ChannelRunnerFactory",
    "HandlerFactory",
    "create_app",
    "main",
]
