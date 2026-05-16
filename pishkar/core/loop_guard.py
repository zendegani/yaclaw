"""SHA256 loop detector.

Tracks recent `(tool_name, args_hash)` pairs across the agent loop. Trips
when the same pair appears `threshold` times within the last `window`
turns. Defaults: 5 occurrences in the last 10 turns. Configurable per
tool via `per_tool_threshold`.

Same hash 3× in a row would be too aggressive — legitimate tools poll
(`tail` a log, watch a file). 5/10 is the documented safe default.
"""

from collections import deque
from typing import Any

from pishkar.workspace.store import args_hash


class LoopGuard:
    def __init__(
        self,
        *,
        window: int = 10,
        threshold: int = 5,
        per_tool_threshold: dict[str, int] | None = None,
    ) -> None:
        self._window = window
        self._threshold = threshold
        self._per_tool = per_tool_threshold or {}
        self._history: deque[tuple[str, str]] = deque(maxlen=window)

    def record(self, tool_name: str, args: dict[str, Any]) -> None:
        self._history.append((tool_name, args_hash(args)))

    def is_looping(self, tool_name: str, args: dict[str, Any]) -> bool:
        key = (tool_name, args_hash(args))
        threshold = self._per_tool.get(tool_name, self._threshold)
        # Including the about-to-execute call: count prior occurrences + 1.
        count = sum(1 for k in self._history if k == key) + 1
        return count >= threshold

    def reset(self) -> None:
        self._history.clear()


__all__ = ["LoopGuard"]
