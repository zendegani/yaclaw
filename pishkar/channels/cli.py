"""In-process CLI channel — stdin lines in, rendered events out.

The shipped TUI is really a WebSocket client (see `channels/ws.py`);
this in-process variant exists so the runtime can be driven directly
from a terminal without a server, and so tests can exercise the Channel
contract against ordinary streams.
"""

import asyncio
import sys
from collections.abc import AsyncIterator
from typing import TextIO

from pishkar.core.events import ContentBlockDelta, Event, TextDelta, ToolResult, TurnEnd
from pishkar.core.messages import InboundMessage


def _render(event: Event) -> str | None:
    if isinstance(event, ContentBlockDelta) and isinstance(event.delta, TextDelta):
        return event.delta.text
    if isinstance(event, ToolResult):
        prefix = "[tool error] " if event.is_error else "[tool] "
        return f"\n{prefix}{event.content}\n"
    if isinstance(event, TurnEnd):
        return f"\n[end: {event.stop_reason}]\n"
    return None


class CLIChannel:
    name = "cli"

    def __init__(
        self,
        *,
        user_id: str,
        session_id: str,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
    ) -> None:
        self._user_id = user_id
        self._session_id = session_id
        self._stdin = stdin or sys.stdin
        self._stdout = stdout or sys.stdout
        self._closed = False

    async def inbound(self) -> AsyncIterator[InboundMessage]:
        while not self._closed:
            line = await asyncio.to_thread(self._stdin.readline)
            if not line:
                return
            content = line.rstrip("\n")
            if not content:
                continue
            yield InboundMessage(
                user_id=self._user_id,
                session_id=self._session_id,
                channel=self.name,
                content=content,
            )

    async def send_event(self, event: Event) -> None:
        if self._closed:
            return
        rendered = _render(event)
        if rendered is None:
            return
        await asyncio.to_thread(self._write, rendered)

    def _write(self, s: str) -> None:
        self._stdout.write(s)
        self._stdout.flush()

    async def close(self) -> None:
        self._closed = True


__all__ = ["CLIChannel"]
