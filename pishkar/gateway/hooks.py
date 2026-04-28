"""Hooks layer — fail-open observability fan-out.

Four named events fire at the seams of the agent loop and tool runner:

* `before_tool(tool_name, args)`
* `on_tool_result(tool_name, args, result)`
* `after_llm(turn_id, session_id, stop_reason, input_tokens, output_tokens)`
* `on_turn_complete(turn_id, session_id, stop_reason)`

Handlers are registered per event with `manager.on(event, fn)`. Emission
schedules each handler via `asyncio.create_task` and returns immediately;
exceptions in handlers are swallowed. The agent loop never blocks waiting
on a hook, even if the LangFuse exporter is down or slow.

Tests can call `await manager.drain()` to wait for outstanding handler
tasks before asserting side effects.
"""

import asyncio
import inspect
from collections import defaultdict
from collections.abc import Callable
from typing import Any

HookHandler = Callable[..., Any]

BEFORE_TOOL = "before_tool"
ON_TOOL_RESULT = "on_tool_result"
AFTER_LLM = "after_llm"
ON_TURN_COMPLETE = "on_turn_complete"

KNOWN_EVENTS = frozenset({BEFORE_TOOL, ON_TOOL_RESULT, AFTER_LLM, ON_TURN_COMPLETE})


class HookManager:
    def __init__(self) -> None:
        self._handlers: dict[str, list[HookHandler]] = defaultdict(list)
        self._tasks: set[asyncio.Task[Any]] = set()

    def on(self, event: str, fn: HookHandler) -> None:
        self._handlers[event].append(fn)

    def emit(self, event: str, /, **payload: Any) -> None:
        handlers = self._handlers.get(event)
        if not handlers:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No loop — drop the event rather than raise. Hooks are best-effort.
            return
        for fn in handlers:
            task = loop.create_task(self._run_safe(fn, payload))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _run_safe(self, fn: HookHandler, payload: dict[str, Any]) -> None:
        try:
            result = fn(**payload)
            if inspect.isawaitable(result):
                await result
        except BaseException:  # noqa: BLE001 — fail-open is the contract
            pass

    async def drain(self) -> None:
        if not self._tasks:
            return
        await asyncio.gather(*list(self._tasks), return_exceptions=True)


__all__ = [
    "AFTER_LLM",
    "BEFORE_TOOL",
    "HookHandler",
    "HookManager",
    "KNOWN_EVENTS",
    "ON_TOOL_RESULT",
    "ON_TURN_COMPLETE",
]
