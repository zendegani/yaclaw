import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from pishkar.core.agent import run_turn
from pishkar.core.events import TextBlock, TextDelta, TurnEnd
from pishkar.core.messages import InboundMessage
from pishkar.gateway.hooks import HookManager
from pishkar.observability.sqlite_sink import SqliteSink
from pishkar.providers.base import ModelProvider, ProviderChunk, Usage
from pishkar.tools.registry import ToolRegistry
from pishkar.tools.runner import SubprocessToolRunner
from pishkar.workspace.store import SessionStore


@pytest.fixture
async def store(tmp_path: Path):
    s = SessionStore(tmp_path / "sessions.db")
    await s.open()
    try:
        yield s
    finally:
        await s.close()


async def test_write_event_persists_payload(store: SessionStore) -> None:
    sink = SqliteSink(store)
    ev = TurnEnd(turn_id="t1", session_id="s1", stop_reason="end_turn")
    await sink.write_event(ev)

    rows = await store.events_after("s1")
    assert len(rows) == 1
    assert rows[0]["event_id"] == ev.event_id
    assert rows[0]["type"] == "turn_end"
    assert json.loads(rows[0]["payload_json"])["stop_reason"] == "end_turn"


async def test_events_after_replays_in_order(store: SessionStore) -> None:
    sink = SqliteSink(store)
    e1 = TurnEnd(turn_id="t1", session_id="s1", stop_reason="end_turn")
    e2 = TurnEnd(turn_id="t2", session_id="s1", stop_reason="max_turns")
    e3 = TurnEnd(turn_id="t3", session_id="s1", stop_reason="end_turn")
    for ev in (e1, e2, e3):
        await sink.write_event(ev)

    after_e1 = await store.events_after("s1", after_event_id=e1.event_id)
    assert [r["event_id"] for r in after_e1] == [e2.event_id, e3.event_id]


async def test_events_isolated_per_session(store: SessionStore) -> None:
    sink = SqliteSink(store)
    await sink.write_event(TurnEnd(turn_id="t", session_id="s1", stop_reason="end_turn"))
    await sink.write_event(TurnEnd(turn_id="t", session_id="s2", stop_reason="end_turn"))
    assert len(await store.events_after("s1")) == 1
    assert len(await store.events_after("s2")) == 1


async def test_write_event_idempotent_on_event_id(store: SessionStore) -> None:
    sink = SqliteSink(store)
    ev = TurnEnd(turn_id="t", session_id="s", stop_reason="end_turn")
    await sink.write_event(ev)
    await sink.write_event(ev)  # same event_id; second is ignored
    assert len(await store.events_after("s")) == 1


# ---- attach: token spend recorded from after_llm ---------------------------


class _Provider(ModelProvider):
    def __init__(self, chunks: list[ProviderChunk]) -> None:
        self._chunks = chunks

    async def stream(self, **_: Any) -> AsyncIterator[ProviderChunk]:
        for c in self._chunks:
            yield c


async def test_attach_records_token_spend_from_after_llm(store: SessionStore) -> None:
    hm = HookManager()
    sink = SqliteSink(store)
    sink.attach(hm)

    provider = _Provider([
        ProviderChunk(text="ok"),
        ProviderChunk(stop_reason="stop"),
        ProviderChunk(usage=Usage(input_tokens=12, output_tokens=8)),
    ])
    [_ async for _ in run_turn(
        user_message=InboundMessage(user_id="ali", session_id="s1",
                                    channel="cli", content="hi"),
        history=[],
        provider=provider,
        runner=SubprocessToolRunner(ToolRegistry()),
        hooks=hm,
        model="claude-opus",
    )]
    await hm.drain()

    inp, out = await store.tokens_spent_since("ali", "1970-01-01T00:00:00+00:00")
    assert (inp, out) == (12, 8)


async def test_attach_skips_when_no_usage(store: SessionStore) -> None:
    hm = HookManager()
    sink = SqliteSink(store)
    sink.attach(hm)

    provider = _Provider([
        ProviderChunk(text="ok"),
        ProviderChunk(stop_reason="stop"),
        # no usage chunk
    ])
    [_ async for _ in run_turn(
        user_message=InboundMessage(user_id="ali", session_id="s1",
                                    channel="cli", content="hi"),
        history=[],
        provider=provider,
        runner=SubprocessToolRunner(ToolRegistry()),
        hooks=hm,
    )]
    await hm.drain()
    inp, out = await store.tokens_spent_since("ali", "1970-01-01T00:00:00+00:00")
    assert (inp, out) == (0, 0)


async def test_write_event_round_trips_text_delta(store: SessionStore) -> None:
    """Smoke: any Event subclass should serialize cleanly."""
    from pishkar.core.events import ContentBlockDelta

    sink = SqliteSink(store)
    ev = ContentBlockDelta(
        turn_id="t", session_id="s", index=0, delta=TextDelta(text="hello")
    )
    await sink.write_event(ev)
    rows = await store.events_after("s")
    payload = json.loads(rows[0]["payload_json"])
    assert payload["delta"]["type"] == "text_delta"
    assert payload["delta"]["text"] == "hello"
    # TextBlock unused — silence import lint by referencing it.
    assert TextBlock(text="x").type == "text"
