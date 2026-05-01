"""Per-user fan-out for cross-channel events.

The Gateway routes events by `session_id` (one chat thread). Cross-cutting
events like `SessionChanged` need to reach *every* live channel for a given
user — the laptop tab, the phone bot, the watch app — regardless of which
session each is currently attached to. This registry holds a `user_id`
keyed list of send callables; both the WebSocket endpoint and the Telegram
runner register/unregister as channels come and go.

Failures inside a single channel's send are swallowed so one dead socket
doesn't block fan-out to the rest.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from pishkar.core.events import Event

logger = logging.getLogger(__name__)

SendEvent = Callable[[Event], Awaitable[None]]


class UserChannelRegistry:
    def __init__(self) -> None:
        self._channels: dict[str, list[tuple[str, SendEvent]]] = {}
        self._lock = asyncio.Lock()

    async def register(
        self, user_id: str, session_id: str, send: SendEvent
    ) -> None:
        async with self._lock:
            self._channels.setdefault(user_id, []).append((session_id, send))

    async def unregister(self, user_id: str, send: SendEvent) -> None:
        async with self._lock:
            entries = self._channels.get(user_id)
            if not entries:
                return
            self._channels[user_id] = [(sid, s) for sid, s in entries if s is not send]
            if not self._channels[user_id]:
                self._channels.pop(user_id, None)

    async def broadcast(
        self, user_id: str, event: Event, *, exclude_session: str | None = None
    ) -> None:
        async with self._lock:
            entries = list(self._channels.get(user_id, []))
        for sid, send in entries:
            if sid == exclude_session:
                continue
            try:
                await send(event)
            except Exception:
                logger.exception("user_registry: send failed for user %s", user_id)


__all__ = ["UserChannelRegistry"]
