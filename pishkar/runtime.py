"""Compose the real agent handler used by `pishkar.server.main()`.

The Gateway accepts any `Handler: (InboundMessage) -> AsyncIterator[Event]`.
This module wires the production stack — ToolRegistry + SubprocessToolRunner
+ ModelProvider + per-session history + `run_turn` — into one such callable.

Per-session history is held in-process: a `dict[session_id, list[message]]`.
That is good enough to try Pishkar end-to-end. Persisting/replaying history
across restarts lives behind the `SessionStore` seam and lands later.
"""

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
from pishkar.tools.approval_gate import ApprovalGate
from pishkar.tools.bash import bash
from pishkar.tools.fs import read_file, write_file
from pishkar.tools.http import http
from pishkar.tools.registry import ToolRegistry
from pishkar.tools.runner import SubprocessToolRunner
from pishkar.workspace.loader import WorkspaceLoader, compose_system_prompt
from pishkar.workspace.store import SessionStore

# Tools that need user approval before each call. Read-only operations
# stay off this list; sensitive ones (state-mutating, network, shell)
# are gated.
DEFAULT_GATED_TOOLS: frozenset[str] = frozenset({"write_file", "http", "bash"})

DEFAULT_SYSTEM = (
    "You are Pishkar, a personal AI butler. Be concise and direct. "
    "Use tools when they help; otherwise just answer."
)


def build_handler(
    *,
    provider: ModelProvider,
    model: str,
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
    registry = registry or _default_registry()
    histories: dict[str, list[dict[str, Any]]] = {}
    gates: dict[str, ApprovalGate] = {}
    tool_schemas = registry.schemas("openai")
    interrupted = interrupted_sessions if interrupted_sessions is not None else set()

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
        return SubprocessToolRunner(registry, hooks=hooks, approval_fn=gate.check)

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
                tool_schemas=tool_schemas,
                system=turn_system,
                model=model,
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


def _default_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register_many(read_file, write_file, http, bash)
    return reg


def build_default_provider() -> tuple[ModelProvider, str]:
    """Construct the production LiteLLM router from environment.

    Picks a default model based on which `*_API_KEY` is present (override
    with `PISHKAR_MODEL`). litellm reads the key envs itself.
    """
    model = os.environ.get("PISHKAR_MODEL") or _default_model_for_env()
    model_list: list[dict[str, Any]] = [
        {"model_name": model, "litellm_params": {"model": _litellm_name(model)}},
    ]
    completion = build_router_completion(model_list)
    return LiteLLMProvider(completion), model


# Map known API-key env vars to (default model, litellm prefix).
# Order = priority when multiple keys are set and PISHKAR_MODEL is unset.
PROVIDER_KEYS: tuple[tuple[str, str, str], ...] = (
    ("ANTHROPIC_API_KEY", "claude-opus-4-7", "anthropic"),
    ("OPENAI_API_KEY", "gpt-4o-mini", "openai"),
    ("OPENROUTER_API_KEY", "openrouter/anthropic/claude-3.5-sonnet", "openrouter"),
    ("GROQ_API_KEY", "groq/llama-3.3-70b-versatile", "groq"),
    ("MOONSHOT_API_KEY", "moonshot/moonshot-v1-8k", "moonshot"),
    ("DASHSCOPE_API_KEY", "dashscope/qwen-turbo", "dashscope"),
    ("GEMINI_API_KEY", "gemini-3-flash-preview", "gemini"),
    ("GOOGLE_API_KEY", "gemini-3-flash-preview", "gemini"),
)

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
    {"anthropic", "openai", "gemini", "openrouter", "groq", "moonshot", "dashscope"}
)


def _default_model_for_env() -> str:
    for key, default_model, _ in PROVIDER_KEYS:
        if os.environ.get(key):
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
    "build_default_provider",
    "build_handler",
    "recover_on_startup",
]
