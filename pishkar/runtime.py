"""Compose the real agent handler used by `pishkar.server.main()`.

The Gateway accepts any `Handler: (InboundMessage) -> AsyncIterator[Event]`.
This module wires the production stack — ToolRegistry + SubprocessToolRunner
+ ModelProvider + per-session history + `run_turn` — into one such callable.

Per-session history is held in-process: a `dict[session_id, list[message]]`.
That is good enough to try Pishkar end-to-end. Persisting/replaying history
across restarts lives behind the `SessionStore` seam and lands later.
"""

import logging
import os
from collections.abc import AsyncIterator
from typing import Any

from pishkar.core.agent import run_turn
from pishkar.core.context import current_channel, current_session_id, current_turn_id
from pishkar.core.events import Event
from pishkar.core.messages import InboundMessage
from pishkar.gateway.approval_router import ApprovalRouter
from pishkar.gateway.gateway import Handler
from pishkar.gateway.hooks import HookManager
from pishkar.providers.base import ModelProvider
from pishkar.providers.litellm_provider import LiteLLMProvider, build_router_completion
from pishkar.providers.minimax import is_minimax_model, minimax_acompletion
from pishkar.tools.approval_gate import ApprovalGate
from pishkar.tools.bash import bash
from pishkar.tools.fs import read_file, write_file
from pishkar.tools.http import http
from pishkar.tools.plan import plan
from pishkar.tools.read_url import read_url
from pishkar.tools.registry import ToolRegistry
from pishkar.tools.runner import SubprocessToolRunner
from pishkar.tools.search import search
from pishkar.tools.speak import speak
from pishkar.workspace.loader import WorkspaceLoader, compose_system_prompt
from pishkar.workspace.store import SessionStore

logger = logging.getLogger(__name__)

# Tools that need user approval before each call. Read-only operations
# stay off this list; sensitive ones (state-mutating, network, shell)
# are gated.
DEFAULT_GATED_TOOLS: frozenset[str] = frozenset(
    {"write_file", "http", "bash", "read_url", "search"}
)

# Default cap on tool-result bytes fed back into the LLM context. Sized to
# stay comfortably under typical free-tier TPM limits (e.g. Groq Scout at
# 30k tokens/min); raise via `PISHKAR_TOOL_MAX_BYTES` for paid tiers.
DEFAULT_TOOL_MAX_BYTES = 16_000

DEFAULT_SYSTEM = (
    "You are Pishkar, a personal AI butler. Be concise and direct. "
    "Use tools when they help; otherwise just answer.\n\n"
    "For tasks needing more than ~2 tool calls (research + summarize, "
    "multi-file edits, anything you'd want to check off), call the "
    "`plan` tool first with a brief markdown checklist. Revise the plan "
    "as you learn things. It is saved per-session and visible across "
    "turns so you don't lose track."
)


def build_handler(
    *,
    provider: ModelProvider,
    model: str | ModelSelector,
    registry: ToolRegistry | None = None,
    runner: SubprocessToolRunner | None = None,
    hooks: HookManager | None = None,
    system: str = DEFAULT_SYSTEM,
    workspace_loader: WorkspaceLoader | None = None,
    interrupted_sessions: set[str] | None = None,
    approval_router: ApprovalRouter | None = None,
    gated_tools: frozenset[str] = DEFAULT_GATED_TOOLS,
    store: SessionStore | None = None,
) -> Handler:
    selector = model if isinstance(model, ModelSelector) else ModelSelector(default=model)
    registry = registry or _default_registry()
    histories: dict[str, list[dict[str, Any]]] = {}
    gates: dict[str, ApprovalGate] = {}
    interrupted = interrupted_sessions if interrupted_sessions is not None else set()
    tool_max_bytes = _tool_max_bytes_from_env()

    def _make_gate(session_id: str, user_id: str) -> ApprovalGate:
        all_tools = set(registry.names())
        always_allow = all_tools - set(gated_tools)

        async def prompt(tool_name: str, args: dict[str, Any]) -> Any:
            if approval_router is None:
                from pishkar.tools.approval_gate import ApprovalDecision

                return ApprovalDecision.DENY
            return await approval_router.request(
                session_id=current_session_id.get() or session_id,
                turn_id=current_turn_id.get(),
                tool_name=tool_name,
                args=args,
                preferred_channel=current_channel.get() or None,
            )

        return ApprovalGate(
            prompt,
            store=store,
            user_id=user_id,
            session_id=session_id,
            always_allow=always_allow,
        )

    def _runner_for(session_id: str, user_id: str) -> SubprocessToolRunner:
        if runner is not None:
            return runner
        gate = gates.get(session_id)
        if gate is None:
            gate = _make_gate(session_id, user_id)
            gates[session_id] = gate
        return SubprocessToolRunner(
            registry,
            hooks=hooks,
            approval_fn=gate.check,
            default_max_bytes=tool_max_bytes,
        )

    async def _hydrate(session_id: str) -> list[dict[str, Any]]:
        if store is None:
            return []
        rows = await store.session_history(session_id)
        history: list[dict[str, Any]] = []
        for row in rows:
            role = "user" if row["direction"] == "inbound" else "assistant"
            history.append({"role": role, "content": row["content"]})
        return history

    async def _ensure_history(session_id: str) -> list[dict[str, Any]]:
        if session_id in histories:
            return histories[session_id]
        history = await _hydrate(session_id)
        histories[session_id] = history
        return history

    def handler(msg: InboundMessage) -> AsyncIterator[Event]:
        async def gen() -> AsyncIterator[Event]:
            current_channel.set(msg.channel)
            history = await _ensure_history(msg.session_id)
            turn_runner = _runner_for(msg.session_id, msg.user_id)
            # Re-read the workspace per turn so edits the agent makes to
            # USER.md take effect on the next turn without a server restart.
            if workspace_loader is not None:
                ws = workspace_loader.load(msg.user_id)
                turn_system = compose_system_prompt(ws, base=system)
            else:
                turn_system = system
            if msg.session_id in interrupted:
                turn_system = (
                    turn_system
                    + "\n\n# Recovery notice\n\n"
                    + "The previous turn in this session was interrupted before it "
                    + "completed (server crash, restart, or kill). Decide whether to "
                    + "retry the prior request, abandon it, or just acknowledge — the "
                    + "user has not been told."
                )
                interrupted.discard(msg.session_id)
            async for event in run_turn(
                user_message=msg,
                history=history,
                provider=provider,
                runner=turn_runner,
                # Read fresh per turn so tools registered after handler
                # construction (e.g. MCP servers connected during lifespan
                # startup) are visible to the LLM on the very next call.
                tool_schemas=registry.schemas("openai"),
                system=turn_system,
                model=selector.current(),
                hooks=hooks,
            ):
                yield event

        return gen()

    return handler


async def recover_on_startup(store: SessionStore) -> dict[str, Any]:
    """Sweep the DB for state left behind by a crash.

    Marks orphaned `tool_call` rows (status `pending`, no `tool_result`)
    as `interrupted`, and finds turns whose `started_at` has no
    `ended_at`. Returns the set of session ids that had an interrupted
    turn so the next inbound on each gets a recovery system note.
    """
    interrupted_tool_calls = await store.mark_orphan_tool_calls_interrupted()
    interrupted_turn_ids = await store.find_interrupted_turns()
    sessions: set[str] = set()
    for turn_id in interrupted_turn_ids:
        sid = await _session_id_for_turn(store, turn_id)
        if sid:
            sessions.add(sid)
        # Stamp the turn so we don't re-flag it on the next restart.
        await store.end_turn(turn_id, "interrupted")
    return {
        "interrupted_tool_calls": interrupted_tool_calls,
        "interrupted_turn_ids": interrupted_turn_ids,
        "interrupted_sessions": sessions,
    }


async def _session_id_for_turn(store: SessionStore, turn_id: str) -> str | None:
    async with store.db.execute(
        "SELECT session_id FROM turns WHERE turn_id = ?", (turn_id,)
    ) as cur:
        row = await cur.fetchone()
    return row[0] if row else None


def _tool_max_bytes_from_env() -> int:
    raw = os.environ.get("PISHKAR_TOOL_MAX_BYTES")
    if not raw:
        return DEFAULT_TOOL_MAX_BYTES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_TOOL_MAX_BYTES
    return value if value > 0 else DEFAULT_TOOL_MAX_BYTES


def _default_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register_many(
        read_file, write_file, http, bash, read_url, search, plan, speak,
    )
    return reg


def build_default_provider() -> tuple[ModelProvider, str]:
    """Construct the production LiteLLM router from environment.

    The active model and fallback chain come from `PISHKAR_MODEL_1`,
    `PISHKAR_MODEL_2`, … (`PISHKAR_MODEL` is accepted as an alias for
    `_1`). If none are set, the first provider with a usable API key in
    `PROVIDER_KEYS` order wins. Every catalog entry from
    `discover_available_models()` is also registered with LiteLLM so
    `/model` can switch to any of them at runtime without rebuilding.

    MiniMax is handled by a thin sibling client because its chat endpoint
    is `/v1/text/chatcompletion_v2?GroupId=…` — a non-standard path that
    LiteLLM's `openai/` shim can't reach. A dispatcher in front of the
    Router intercepts `minimax/*` models; everything else flows through
    LiteLLM unchanged. The same dispatcher walks the fallback chain on
    failure (auth errors, 429, mid-stream tool-call errors before the
    first chunk), so a flaky primary doesn't sink the turn.
    """
    chain = _read_model_chain() or [_default_model_for_env()]
    primary = chain[0]
    fallbacks = chain[1:]

    candidates: set[str] = set(chain)
    for models in discover_available_models().values():
        candidates.update(models)

    non_minimax = sorted(m for m in candidates if not is_minimax_model(m))
    model_list: list[dict[str, Any]] = [
        {"model_name": m, "litellm_params": {"model": _litellm_name(m)}}
        for m in non_minimax
    ]
    router_completion = build_router_completion(model_list) if model_list else None
    completion = _build_completion_dispatcher(
        router_completion, primary=primary, fallbacks=fallbacks
    )
    return LiteLLMProvider(completion), primary


def _read_model_chain() -> list[str]:
    """Parse `PISHKAR_MODEL` / `PISHKAR_MODEL_1..N` into an ordered chain.

    `PISHKAR_MODEL` and `PISHKAR_MODEL_1` are synonyms (first non-empty
    wins). The chain walks until the next index is missing — gaps end the
    chain rather than being skipped, so a typo is loud, not silent.
    """
    chain: list[str] = []
    first = os.environ.get("PISHKAR_MODEL_1") or os.environ.get("PISHKAR_MODEL")
    if not first:
        return chain
    chain.append(first)
    i = 2
    while True:
        nxt = os.environ.get(f"PISHKAR_MODEL_{i}")
        if not nxt:
            return chain
        chain.append(nxt)
        i += 1


def _build_completion_dispatcher(
    router_completion: Any | None,
    *,
    primary: str | None = None,
    fallbacks: list[str] | None = None,
) -> Any:
    """Route `minimax/*` to the MiniMax adapter, pass everything else to
    the LiteLLM Router, and walk the fallback chain on failure.

    Fallbacks only apply when the requested model is the configured
    primary — an explicit `/model` switch should mean exactly that one
    model, not a chain. Falling back covers two error classes: failures
    raised before the stream is awaited (auth, model-not-found, 429
    from the connect path) and failures raised on the first chunk
    (Groq's `tool_use_failed`, malformed SSE). After at least one chunk
    has been yielded the turn is committed and errors propagate."""
    fallbacks = fallbacks or []

    async def _call_one(candidate: str, **kwargs: Any) -> Any:
        if is_minimax_model(candidate):
            return await minimax_acompletion(model=candidate, **kwargs)
        if router_completion is None:
            raise RuntimeError(
                f"No LiteLLM Router is configured for non-MiniMax model "
                f"{candidate!r}; set an API key for the matching provider."
            )
        return await router_completion(model=candidate, **kwargs)

    async def dispatch(*, model: str, **kwargs: Any) -> Any:
        chain = [model, *fallbacks] if model == primary and fallbacks else [model]
        if len(chain) == 1:
            return await _call_one(model, **kwargs)

        last_err: BaseException | None = None
        for candidate in chain:
            try:
                stream = await _call_one(candidate, **kwargs)
                it = stream.__aiter__()
                try:
                    first = await it.__anext__()
                except StopAsyncIteration:
                    return _empty_stream()
                return _replay(first, it)
            except Exception as exc:  # noqa: BLE001 — we re-raise the last one
                last_err = exc
                logger.warning(
                    "model %r failed (%s); trying next in chain",
                    candidate, exc.__class__.__name__,
                )
                continue
        assert last_err is not None
        raise last_err

    return dispatch


async def _replay(first: Any, iterator: Any) -> AsyncIterator[Any]:
    yield first
    async for chunk in iterator:
        yield chunk


async def _empty_stream() -> AsyncIterator[Any]:
    # `if False: yield` keeps this an async generator without an
    # unreachable-code warning. Hit when the underlying provider closes
    # the stream with zero chunks — rare but possible.
    if False:
        yield None


# Map known API-key env vars to (default model, litellm prefix).
# Order = priority when multiple keys are set and PISHKAR_MODEL is unset.
PROVIDER_KEYS: tuple[tuple[str, str, str], ...] = (
    ("ANTHROPIC_API_KEY", "claude-opus-4-7", "anthropic"),
    ("OPENAI_API_KEY", "gpt-4o-mini", "openai"),
    ("OPENROUTER_API_KEY", "openrouter/anthropic/claude-3.5-sonnet", "openrouter"),
    ("GROQ_API_KEY", "groq/openai/gpt-oss-120b", "groq"),
    ("MOONSHOT_API_KEY", "moonshot/moonshot-v1-8k", "moonshot"),
    ("DASHSCOPE_API_KEY", "dashscope/qwen-turbo", "dashscope"),
    ("GEMINI_API_KEY", "gemini-3-flash-preview", "gemini"),
    ("GOOGLE_API_KEY", "gemini-3-flash-preview", "gemini"),
    ("MINIMAX_API_KEY", "minimax/MiniMax-M2.7", "minimax"),
)

# Providers that need a second env var beyond their API key to be usable.
# Used by `discover_available_models` and `_default_model_for_env` so a
# half-configured provider doesn't get listed (and then 500 at call time).
EXTRA_ENV_REQUIRED: dict[str, tuple[str, ...]] = {
    "minimax": ("MINIMAX_GROUP_ID",),
}

# Curated catalog of swappable models per provider, surfaced by the
# Telegram `/model` command. Entries are in litellm naming so they go
# straight to the Router. Add to this list as new options come up — it
# is intentionally compact rather than exhaustive.
KNOWN_MODELS_BY_PROVIDER: dict[str, tuple[str, ...]] = {
    "anthropic": (
        "claude-opus-4-7",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
    ),
    "openai": (
        "gpt-4o",
        "gpt-4o-mini",
    ),
    "gemini": (
        "gemini-2.5-pro",
        "gemini-2.5-flash",
    ),
    "groq": (
        "groq/meta-llama/llama-4-scout-17b-16e-instruct",
        "groq/openai/gpt-oss-120b",
        "groq/openai/gpt-oss-20b",
        "groq/qwen/qwen3-32b",
    ),
    "moonshot": (
        "moonshot/moonshot-v1-8k",
        "moonshot/moonshot-v1-32k",
        "moonshot/moonshot-v1-128k",
    ),
    "dashscope": (
        "dashscope/qwen-turbo",
        "dashscope/qwen-plus",
        "dashscope/qwen-max",
    ),
    "minimax": (
        "minimax/MiniMax-M2.7",
        "minimax/MiniMax-M2",
    ),
}


def discover_available_models() -> dict[str, list[str]]:
    """Return `{provider: [model, …]}` for providers whose API key is set.

    Used to populate the Telegram `/provider` and `/model` keyboards and
    to register the LiteLLM Router with every model the user could pick."""
    available: dict[str, list[str]] = {}
    for env_key, _, provider in PROVIDER_KEYS:
        if not os.environ.get(env_key):
            continue
        if not all(os.environ.get(e) for e in EXTRA_ENV_REQUIRED.get(provider, ())):
            continue
        models = KNOWN_MODELS_BY_PROVIDER.get(provider)
        if not models:
            continue
        available.setdefault(provider, list(models))
    return available


def provider_for_model(model: str) -> str | None:
    """Reverse-lookup the provider that owns a given model string."""
    for prov, models in KNOWN_MODELS_BY_PROVIDER.items():
        if model in models:
            return prov
    return None


class ModelSelector:
    """Small mutable holder for the active model.

    Shared between `build_handler` (reads `current()` per turn) and
    `TelegramBotRunner` (writes via `/model`). Defaults to the model
    chosen by `build_default_provider`; `set_model` validates against
    `available()` so a typo at the keyboard can't push us off-catalog."""

    def __init__(
        self,
        default: str,
        available: dict[str, list[str]] | None = None,
    ) -> None:
        self._default = default
        self._current = default
        self._available = available if available is not None else discover_available_models()

    def current(self) -> str:
        return self._current

    def default(self) -> str:
        return self._default

    def available(self) -> dict[str, list[str]]:
        return {prov: list(models) for prov, models in self._available.items()}

    def models_for(self, provider: str) -> list[str]:
        return list(self._available.get(provider, []))

    def set_model(self, model: str) -> bool:
        for models in self._available.values():
            if model in models:
                self._current = model
                return True
        return False

    def reset(self) -> None:
        self._current = self._default

# Bare model-name prefix → litellm provider prefix. Lets users say
# `claude-opus-4-7` instead of `anthropic/claude-opus-4-7`.
_BARE_PREFIX_TO_PROVIDER: tuple[tuple[str, str], ...] = (
    ("claude-", "anthropic"),
    ("gpt-", "openai"),
    ("o1-", "openai"),
    ("o3-", "openai"),
    ("gemini-", "gemini"),
    ("llama-", "groq"),
    ("mixtral-", "groq"),
    ("moonshot-", "moonshot"),
    ("kimi-", "moonshot"),
    ("qwen-", "dashscope"),
)

KNOWN_PROVIDERS: frozenset[str] = frozenset(
    {"anthropic", "openai", "gemini", "openrouter", "groq", "moonshot",
     "dashscope", "minimax"}
)


def _default_model_for_env() -> str:
    for key, default_model, provider in PROVIDER_KEYS:
        if not os.environ.get(key):
            continue
        if not all(os.environ.get(e) for e in EXTRA_ENV_REQUIRED.get(provider, ())):
            continue
        return default_model
    return "claude-opus-4-7"


def _litellm_name(model: str) -> str:
    """Normalize a model name to litellm's `<provider>/<model>` shape."""
    head = model.split("/", 1)[0]
    if head in KNOWN_PROVIDERS:
        return model
    for prefix, provider in _BARE_PREFIX_TO_PROVIDER:
        if model.startswith(prefix):
            return f"{provider}/{model}"
    return model


__all__ = [
    "DEFAULT_SYSTEM",
    "KNOWN_MODELS_BY_PROVIDER",
    "ModelSelector",
    "build_default_provider",
    "build_handler",
    "discover_available_models",
    "provider_for_model",
    "recover_on_startup",
]
