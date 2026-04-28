"""Channel Protocol — bidirectional transport between users and the Gateway.

A Channel produces `InboundMessage`s for the Gateway to consume and
accepts `Event`s to send back to the user. Channels know nothing about
the agent loop; they translate between transport-specific framing
(WebSocket frames, terminal lines, Telegram updates, …) and the typed
runtime objects.
"""

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from pishkar.core.events import Event
from pishkar.core.messages import InboundMessage


@runtime_checkable
class Channel(Protocol):
    name: str

    def inbound(self) -> AsyncIterator[InboundMessage]: ...

    async def send_event(self, event: Event) -> None: ...

    async def close(self) -> None: ...


__all__ = ["Channel"]
