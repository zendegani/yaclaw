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
) -> Handler:
    registry = registry or _default_registry()
    runner = runner or SubprocessToolRunner(registry, hooks=hooks)
    histories: dict[str, list[dict[str, Any]]] = {}
    tool_schemas = registry.schemas("openai")

    def handler(msg: InboundMessage) -> AsyncIterator[Event]:
        history = histories.setdefault(msg.session_id, [])
        return run_turn(
            user_message=msg,
            history=history,
            provider=provider,
            runner=runner,
            tool_schemas=tool_schemas,
            system=system,
            model=model,
            hooks=hooks,
        )

    return handler


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


__all__ = ["build_handler", "build_default_provider", "DEFAULT_SYSTEM"]
