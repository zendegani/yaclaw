"""WebSocket channel — wraps any FastAPI-compatible WebSocket.

The socket is duck-typed: anything exposing async `receive_text()` and
`send_text()` works, including `starlette.websockets.WebSocket` and the
fakes used in tests. This keeps the channel testable without pulling
FastAPI into the unit-test path.

Inbound JSON frames are parsed into `InboundMessage`. Outbound `Event`s
are serialized via `model_dump_json()`. On reconnect the client may pass
a `last_event_id` query/cookie to the server, which replays missed
events from the SQLite event log; that wiring lives in the gateway, not
here — the channel just streams whatever events it is given.
"""

import json
from collections.abc import AsyncIterator
from typing import Any, Protocol

from pydantic import ValidationError

from pishkar.core.events import Event
from pishkar.core.messages import InboundMessage


class _SocketLike(Protocol):
    async def receive_text(self) -> str: ...
    async def send_text(self, data: str) -> None: ...
    async def close(self) -> None: ...


class WebSocketDisconnect(Exception):
    """Raised by a socket when the peer disconnects. Channels translate
    this into a clean end-of-stream on `inbound()`."""


class WebSocketChannel:
    name = "ws"

    def __init__(
        self,
        socket: _SocketLike,
        *,
        user_id: str,
        session_id: str,
        disconnect_exc: type[BaseException] = WebSocketDisconnect,
    ) -> None:
        self._socket = socket
        self._user_id = user_id
        self._session_id = session_id
        self._disconnect_exc = disconnect_exc
        self._closed = False

    async def inbound(self) -> AsyncIterator[InboundMessage]:
        while not self._closed:
            try:
                raw = await self._socket.receive_text()
            except self._disconnect_exc:
                return
            try:
                payload: dict[str, Any] = json.loads(raw)
            except json.JSONDecodeError:
                continue
            payload.setdefault("user_id", self._user_id)
            payload.setdefault("session_id", self._session_id)
            payload.setdefault("channel", self.name)
            try:
                yield InboundMessage.model_validate(payload)
            except ValidationError:
                continue

    async def send_event(self, event: Event) -> None:
        if self._closed:
            return
        await self._socket.send_text(event.model_dump_json())

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._socket.close()


__all__ = ["WebSocketChannel", "WebSocketDisconnect"]
