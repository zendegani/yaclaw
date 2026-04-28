"""Gateway — routes inbound messages to a per-session handler and fans
outbound events back to the channels attached to that session.

Day-one queueing is in-memory (`asyncio.Queue`), but every inbound is
also persisted to `messages` with `delivered_at = NULL`; on `start()`
the gateway re-enqueues anything still undelivered. That gives us the
SQLite-backed queue from day one without paying the latency of a DB
read on every dispatch.

The handler is injected (`async def handler(msg) -> AsyncIterator[Event]`)
so the gateway stays oblivious to the agent loop. The hook manager is
exposed but not emitted into here — handlers and runner own emission;
the gateway just lets callers register listeners through one place.

Concurrency model: one async task per channel pumps inbound, one
worker task per session drains its own queue serially. Within a
session we never run two turns in parallel; across sessions we do.
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

from pishkar.channels.base import Channel
from pishkar.core.events import Event
from pishkar.core.messages import InboundMessage
from pishkar.gateway.hooks import HookManager
from pishkar.workspace.store import SessionStore

Handler = Callable[[InboundMessage], AsyncIterator[Event]]


class Gateway:
    def __init__(
        self,
        *,
        store: SessionStore,
        handler: Handler,
        hooks: HookManager | None = None,
    ) -> None:
        self._store = store
        self._handler = handler
        self._hooks = hooks or HookManager()
        self._channels: dict[str, list[Channel]] = {}
        self._session_queues: dict[str, asyncio.Queue[InboundMessage]] = {}
        self._session_workers: dict[str, asyncio.Task[None]] = {}
        self._pump_tasks: set[asyncio.Task[None]] = set()
        self._stopped = False

    @property
    def hooks(self) -> HookManager:
        return self._hooks

    def attach_channel(self, session_id: str, channel: Channel) -> None:
        self._channels.setdefault(session_id, []).append(channel)
        self._ensure_session_worker(session_id)
        task = asyncio.create_task(self._pump(channel))
        self._pump_tasks.add(task)
        task.add_done_callback(self._pump_tasks.discard)

    async def submit(self, msg: InboundMessage) -> None:
        """Inject a message directly (used by triggers)."""
        await self._store.enqueue_inbound(msg)
        await self._enqueue(msg)

    async def start(self) -> None:
        """Re-enqueue anything not delivered before the last shutdown."""
        for msg in await self._store.fetch_undelivered_inbound():
            await self._enqueue(msg)

    async def stop(self) -> None:
        self._stopped = True
        for q in self._session_queues.values():
            await q.put(_SHUTDOWN)  # type: ignore[arg-type]
        workers = list(self._session_workers.values())
        pumps = list(self._pump_tasks)
        for t in workers + pumps:
            t.cancel()
        await asyncio.gather(*workers, *pumps, return_exceptions=True)

    # ---- internals -------------------------------------------------------

    def _ensure_session_worker(self, session_id: str) -> None:
        if session_id in self._session_workers:
            return
        q: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self._session_queues[session_id] = q
        self._session_workers[session_id] = asyncio.create_task(
            self._worker(session_id, q)
        )

    async def _enqueue(self, msg: InboundMessage) -> None:
        self._ensure_session_worker(msg.session_id)
        await self._session_queues[msg.session_id].put(msg)

    async def _pump(self, channel: Channel) -> None:
        async for msg in channel.inbound():
            if self._stopped:
                return
            await self._store.enqueue_inbound(msg)
            await self._enqueue(msg)

    async def _worker(self, session_id: str, q: asyncio.Queue[InboundMessage]) -> None:
        while True:
            msg = await q.get()
            if msg is _SHUTDOWN:  # type: ignore[comparison-overlap]
                return
            await self._dispatch(msg)

    async def _dispatch(self, msg: InboundMessage) -> None:
        channels = list(self._channels.get(msg.session_id, []))
        try:
            async for event in self._handler(msg):
                for ch in channels:
                    try:
                        await ch.send_event(event)
                    except BaseException:  # noqa: BLE001 — one bad channel can't kill the turn
                        pass
        finally:
            await self._store.mark_delivered(msg.message_id)


_SHUTDOWN: Any = object()


__all__ = ["Gateway", "Handler"]
