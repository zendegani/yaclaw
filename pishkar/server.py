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

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

logger = logging.getLogger(__name__)

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from pishkar.channels.ws import WebSocketChannel
from pishkar.channels.ws import WebSocketDisconnect as ChannelWebSocketDisconnect
from pishkar.core.events import Event, TurnEnd, TurnStart, UserMessage
from pishkar.core.messages import InboundMessage
from pishkar.gateway.approval_router import ApprovalRouter
from pishkar.gateway.gateway import Gateway, Handler
from pishkar.gateway.hooks import HookManager
from pishkar.observability.sqlite_sink import SqliteSink
from pishkar.tools.approval_gate import ApprovalDecision
from pishkar.workspace.store import SessionStore

HandlerFactory = Callable[[], Handler]


def create_app(
    *,
    store: SessionStore,
    handler: Handler,
    hooks: HookManager | None = None,
    recovery_target: set[str] | None = None,
    approval_router: ApprovalRouter | None = None,
) -> FastAPI:
    hooks = hooks or HookManager()
    sink = SqliteSink(store)
    sink.attach(hooks)

    gateway = Gateway(store=store, handler=_wrap_handler(handler, sink), hooks=hooks)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await store.open()
        if recovery_target is not None:
            from pishkar.runtime import recover_on_startup

            report = await recover_on_startup(store)
            recovery_target.update(report["interrupted_sessions"])
        await gateway.start()
        try:
            yield
        finally:
            await gateway.stop()
            await store.close()

    app = FastAPI(lifespan=lifespan)
    app.state.store = store
    app.state.gateway = gateway
    app.state.hooks = hooks

    @app.websocket("/ws/{user_id}/{session_id}")
    async def _ws(
        socket: WebSocket,
        user_id: str,
        session_id: str,
        last_event_id: str | None = Query(default=None),
    ) -> None:
        await socket.accept()
        await _replay(socket, store, session_id, last_event_id)
        channel = WebSocketChannel(
            _StarletteSocketAdapter(socket),
            user_id=user_id,
            session_id=session_id,
            disconnect_exc=ChannelWebSocketDisconnect,
            control_handler=_make_control_handler(approval_router, session_id),
        )
        gateway.register_channel(session_id, channel)
        if approval_router is not None:
            approval_router.bind(session_id, channel.send_event)
        try:
            async for msg in channel.inbound():
                await gateway.submit(msg)
        except WebSocketDisconnect:
            pass
        finally:
            if approval_router is not None:
                approval_router.unbind(session_id)
            gateway.detach_channel(session_id, channel)
            await channel.close()

    return app


def _make_control_handler(
    approval_router: ApprovalRouter | None, session_id: str
):
    async def handle(payload: dict) -> None:
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


def _wrap_handler(handler: Handler, sink: SqliteSink) -> Handler:
    """Echo the user's message as an event, then tee every assistant
    event through the always-on SQLite log so replay rebuilds the full
    chat (user side included).

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
            )
            await sink.write_event(user_event)
            yield user_event
            try:
                async for event in handler(msg):
                    await sink.write_event(event)
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
) -> None:
    rows = await store.events_after(session_id, after_event_id=last_event_id)
    for row in rows:
        await socket.send_text(row["payload_json"])


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
    if has_key:
        from pishkar.runtime import build_default_provider, build_handler
        from pishkar.workspace.loader import WorkspaceLoader

        loader = WorkspaceLoader(base_dir=db_path.parent)
        loader.ensure_starter(os.environ.get("PISHKAR_USER", "ali"))
        provider, model = build_default_provider()
        handler = build_handler(
            provider=provider,
            model=model,
            workspace_loader=loader,
            interrupted_sessions=interrupted,
            approval_router=approval_router,
            store=store,
        )
    else:
        handler = _default_handler  # echo stub keeps the server bootable offline

    app = create_app(
        store=store,
        handler=handler,
        recovery_target=interrupted,
        approval_router=approval_router,
    )
    uvicorn.run(app, host="127.0.0.1", port=8765)


if __name__ == "__main__":
    main()


__all__ = ["HandlerFactory", "create_app", "main"]
