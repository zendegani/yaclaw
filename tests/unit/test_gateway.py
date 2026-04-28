import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from pishkar.core.events import Event, TurnEnd, TurnStart
from pishkar.core.messages import InboundMessage
from pishkar.gateway.gateway import Gateway
from pishkar.gateway.hooks import HookManager
from pishkar.workspace.store import SessionStore


@pytest.fixture
async def store(tmp_path: Path):
    s = SessionStore(tmp_path / "sessions.db")
    await s.open()
    try:
        yield s
    finally:
        await s.close()


def _msg(session: str = "s1", content: str = "hi", user: str = "ali") -> InboundMessage:
    return InboundMessage(user_id=user, session_id=session, channel="test", content=content)


def _echo_handler(msg: InboundMessage) -> AsyncIterator[Event]:
    async def gen() -> AsyncIterator[Event]:
        yield TurnStart(turn_id=msg.message_id, session_id=msg.session_id, turn_index=0)
        yield TurnEnd(turn_id=msg.message_id, session_id=msg.session_id, stop_reason="end_turn")
    return gen()


class FakeChannel:
    def __init__(self, name: str = "fake") -> None:
        self.name = name
        self.sent: list[Event] = []
        self._inbound: asyncio.Queue[InboundMessage | None] = asyncio.Queue()
        self.closed = False

    async def push(self, msg: InboundMessage | None) -> None:
        await self._inbound.put(msg)

    async def inbound(self) -> AsyncIterator[InboundMessage]:
        while True:
            item = await self._inbound.get()
            if item is None:
                return
            yield item

    async def send_event(self, event: Event) -> None:
        self.sent.append(event)

    async def close(self) -> None:
        self.closed = True


# ---- submit() path ---------------------------------------------------------


async def test_submit_dispatches_through_handler(store: SessionStore) -> None:
    gw = Gateway(store=store, handler=_echo_handler)
    ch = FakeChannel()
    gw.attach_channel("s1", ch)

    msg = _msg()
    await gw.submit(msg)
    await asyncio.sleep(0.05)

    assert [e.type for e in ch.sent] == ["turn_start", "turn_end"]
    await gw.stop()


async def test_submit_persists_and_marks_delivered(store: SessionStore) -> None:
    gw = Gateway(store=store, handler=_echo_handler)
    gw.attach_channel("s1", FakeChannel())

    await gw.submit(_msg())
    await asyncio.sleep(0.05)

    assert await store.fetch_undelivered_inbound() == []
    await gw.stop()


# ---- channel pump path -----------------------------------------------------


async def test_channel_inbound_is_pumped(store: SessionStore) -> None:
    gw = Gateway(store=store, handler=_echo_handler)
    ch = FakeChannel()
    gw.attach_channel("s1", ch)

    await ch.push(_msg(content="one"))
    await ch.push(_msg(content="two"))
    await asyncio.sleep(0.1)

    types = [e.type for e in ch.sent]
    assert types == ["turn_start", "turn_end", "turn_start", "turn_end"]
    await gw.stop()


# ---- session isolation -----------------------------------------------------


async def test_events_only_fan_out_to_session_channels(store: SessionStore) -> None:
    gw = Gateway(store=store, handler=_echo_handler)
    ch_a = FakeChannel("a")
    ch_b = FakeChannel("b")
    gw.attach_channel("s1", ch_a)
    gw.attach_channel("s2", ch_b)

    await gw.submit(_msg(session="s1"))
    await asyncio.sleep(0.05)

    assert len(ch_a.sent) == 2
    assert ch_b.sent == []
    await gw.stop()


async def test_per_session_serialization(store: SessionStore) -> None:
    """Two messages on the same session must run sequentially, not interleave."""
    started: list[str] = []
    finished: list[str] = []

    def handler(msg: InboundMessage) -> AsyncIterator[Event]:
        async def gen() -> AsyncIterator[Event]:
            started.append(msg.content)
            yield TurnStart(turn_id=msg.message_id, session_id=msg.session_id, turn_index=0)
            await asyncio.sleep(0.05)
            yield TurnEnd(turn_id=msg.message_id, session_id=msg.session_id, stop_reason="end_turn")
            finished.append(msg.content)
        return gen()

    gw = Gateway(store=store, handler=handler)
    gw.attach_channel("s1", FakeChannel())

    await gw.submit(_msg(content="A"))
    await gw.submit(_msg(content="B"))
    await asyncio.sleep(0.2)

    # B must not start until A finishes.
    assert started == ["A", "B"]
    assert finished == ["A", "B"]
    await gw.stop()


# ---- restart resilience ----------------------------------------------------


async def test_start_resumes_undelivered_messages(store: SessionStore) -> None:
    # Prior process: enqueued but never delivered.
    pre = _msg(content="pre")
    await store.enqueue_inbound(pre)

    gw = Gateway(store=store, handler=_echo_handler)
    ch = FakeChannel()
    gw.attach_channel(pre.session_id, ch)

    await gw.start()
    await asyncio.sleep(0.05)

    assert [e.type for e in ch.sent] == ["turn_start", "turn_end"]
    assert await store.fetch_undelivered_inbound() == []
    await gw.stop()


# ---- channel-error isolation ------------------------------------------------


async def test_one_failing_channel_doesnt_break_others(store: SessionStore) -> None:
    class Exploder(FakeChannel):
        async def send_event(self, event: Event) -> None:
            raise RuntimeError("boom")

    gw = Gateway(store=store, handler=_echo_handler)
    bad = Exploder("bad")
    good = FakeChannel("good")
    gw.attach_channel("s1", bad)
    gw.attach_channel("s1", good)

    await gw.submit(_msg())
    await asyncio.sleep(0.05)

    assert [e.type for e in good.sent] == ["turn_start", "turn_end"]
    await gw.stop()


# ---- hooks seam ------------------------------------------------------------


async def test_default_hook_manager_is_exposed(store: SessionStore) -> None:
    gw = Gateway(store=store, handler=_echo_handler)
    assert isinstance(gw.hooks, HookManager)
    await gw.stop()


async def test_injected_hook_manager_is_used(store: SessionStore) -> None:
    hm = HookManager()
    gw = Gateway(store=store, handler=_echo_handler, hooks=hm)
    assert gw.hooks is hm
    await gw.stop()
