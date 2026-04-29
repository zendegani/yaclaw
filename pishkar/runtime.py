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
from pishkar.core.events import Event
from pishkar.core.messages import InboundMessage
from pishkar.gateway.gateway import Handler
from pishkar.gateway.hooks import HookManager
from pishkar.providers.base import ModelProvider
from pishkar.providers.litellm_provider import LiteLLMProvider, build_router_completion
from pishkar.tools.fs import read_file, write_file
from pishkar.tools.http import http
from pishkar.tools.registry import ToolRegistry
from pishkar.tools.runner import SubprocessToolRunner
from pishkar.workspace.loader import WorkspaceLoader, compose_system_prompt
from pishkar.workspace.store import SessionStore

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
) -> Handler:
    registry = registry or _default_registry()
    runner = runner or SubprocessToolRunner(registry, hooks=hooks)
    histories: dict[str, list[dict[str, Any]]] = {}
    tool_schemas = registry.schemas("openai")
    interrupted = interrupted_sessions if interrupted_sessions is not None else set()

    def handler(msg: InboundMessage) -> AsyncIterator[Event]:
        history = histories.setdefault(msg.session_id, [])
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
        return run_turn(
            user_message=msg,
            history=history,
            provider=provider,
            runner=runner,
            tool_schemas=tool_schemas,
            system=turn_system,
            model=model,
            hooks=hooks,
        )

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
    reg.register_many(read_file, write_file, http)
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


def _default_model_for_env() -> str:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "claude-opus-4-7"
    if os.environ.get("OPENAI_API_KEY"):
        return "gpt-4o-mini"
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return "gemini-2.5-pro"
    return "claude-opus-4-7"


def _litellm_name(model: str) -> str:
    if model.startswith(("claude-", "anthropic/")):
        return model if model.startswith("anthropic/") else f"anthropic/{model}"
    if model.startswith(("gpt-", "openai/")):
        return model if model.startswith("openai/") else f"openai/{model}"
    if model.startswith(("gemini-", "gemini/")):
        return model if model.startswith("gemini/") else f"gemini/{model}"
    return model


__all__ = [
    "DEFAULT_SYSTEM",
    "build_default_provider",
    "build_handler",
    "recover_on_startup",
]
