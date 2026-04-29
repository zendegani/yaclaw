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

    Looks at `PISHKAR_MODEL` (default `claude-opus-4-7`). Anthropic + OpenAI
    keys are read by litellm itself from `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`.
    """
    model = os.environ.get("PISHKAR_MODEL", "claude-opus-4-7")
    model_list: list[dict[str, Any]] = [
        {"model_name": model, "litellm_params": {"model": _litellm_name(model)}},
    ]
    if os.environ.get("OPENAI_API_KEY"):
        model_list.append(
            {"model_name": "gpt-4o-mini", "litellm_params": {"model": "openai/gpt-4o-mini"}},
        )
    fallbacks = [{model: ["gpt-4o-mini"]}] if len(model_list) > 1 else None
    completion = build_router_completion(model_list, fallbacks=fallbacks)
    return LiteLLMProvider(completion), model


def _litellm_name(model: str) -> str:
    if model.startswith(("claude-", "anthropic/")):
        return model if model.startswith("anthropic/") else f"anthropic/{model}"
    if model.startswith(("gpt-", "openai/")):
        return model if model.startswith("openai/") else f"openai/{model}"
    return model


__all__ = ["build_handler", "build_default_provider", "DEFAULT_SYSTEM"]
