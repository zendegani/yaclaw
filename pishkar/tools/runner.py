"""`ToolRunner` Protocol + `SubprocessToolRunner`.

Every tool call routes through a `ToolRunner` so timeout, max-result
size, and approval are enforced uniformly. Day-one runner runs tools
in-process (subprocess only for the `bash` tool itself, via the tool
implementation). A `DockerToolRunner` would slot in by re-implementing
the same Protocol.

`SubprocessToolRunner` is the misleading name kept from the design doc:
"subprocess" refers to the *kind of sandbox* envisioned (versus an
in-process call), not literal `subprocess` invocations here. It dispatches
into `ToolRegistry.call`, wraps the awaitable in `asyncio.wait_for` for
the timeout, and truncates oversized results with a notice.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from pishkar.tools.registry import ToolRegistry

DEFAULT_TIMEOUT_S = 30.0
DEFAULT_MAX_RESULT_BYTES = 1_000_000

ApprovalFn = Callable[[str, dict[str, Any]], Awaitable[bool]]


class ToolResult(BaseModel):
    tool_name: str
    content: str
    is_error: bool = False
    truncated: bool = False
    timed_out: bool = False
    denied: bool = False


@runtime_checkable
class ToolRunner(Protocol):
    async def run(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        timeout_s: float | None = None,
        max_bytes: int | None = None,
    ) -> ToolResult: ...


def _truncate(text: str, max_bytes: int) -> tuple[str, bool]:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text, False
    notice = f"\n\n[truncated: result exceeded {max_bytes} bytes]"
    keep = max_bytes - len(notice.encode("utf-8"))
    keep = max(keep, 0)
    head = encoded[:keep].decode("utf-8", errors="replace")
    return head + notice, True


def _to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return repr(value)


class SubprocessToolRunner(ToolRunner):
    """In-process dispatcher. The "subprocess" name is the runner *kind*
    (sandboxable, swappable for `DockerToolRunner` later) — actual
    process isolation lives in the `bash` tool itself."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        default_timeout_s: float = DEFAULT_TIMEOUT_S,
        default_max_bytes: int = DEFAULT_MAX_RESULT_BYTES,
        approval_fn: ApprovalFn | None = None,
    ) -> None:
        self._registry = registry
        self._default_timeout_s = default_timeout_s
        self._default_max_bytes = default_max_bytes
        self._approval_fn = approval_fn

    async def run(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        timeout_s: float | None = None,
        max_bytes: int | None = None,
    ) -> ToolResult:
        timeout = timeout_s if timeout_s is not None else self._default_timeout_s
        cap = max_bytes if max_bytes is not None else self._default_max_bytes

        if self._approval_fn is not None:
            allowed = await self._approval_fn(tool_name, args)
            if not allowed:
                return ToolResult(
                    tool_name=tool_name,
                    content=f"[denied] user did not approve {tool_name}",
                    is_error=True,
                    denied=True,
                )

        try:
            value = await asyncio.wait_for(
                self._registry.call(tool_name, args), timeout=timeout
            )
        except TimeoutError:
            return ToolResult(
                tool_name=tool_name,
                content=f"[timeout] {tool_name} exceeded {timeout}s",
                is_error=True,
                timed_out=True,
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult(
                tool_name=tool_name,
                content=f"[error] {type(e).__name__}: {e}",
                is_error=True,
            )

        text, truncated = _truncate(_to_text(value), cap)
        return ToolResult(
            tool_name=tool_name,
            content=text,
            truncated=truncated,
        )


__all__ = [
    "DEFAULT_MAX_RESULT_BYTES",
    "DEFAULT_TIMEOUT_S",
    "ApprovalFn",
    "SubprocessToolRunner",
    "ToolResult",
    "ToolRunner",
]
