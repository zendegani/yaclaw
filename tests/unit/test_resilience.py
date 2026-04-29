"""Resilience hardening: turn-lifecycle + tool-call wiring through SqliteSink,
and `recover_on_startup` recovery sweep + synthetic-note injection."""

from pathlib import Path

import pytest

from pishkar.core.events import (
    ContentBlockStart,
    ToolResult,
    ToolUseBlock,
    TurnEnd,
    TurnStart,
)
from pishkar.core.messages import InboundMessage
from pishkar.observability.sqlite_sink import SqliteSink
from pishkar.runtime import build_handler, recover_on_startup
from pishkar.workspace.store import SessionStore


@pytest.fixture
async def store(tmp_path: Path):
    s = SessionStore(tmp_path / "sessions.db")
    await s.open()
    yield s
    await s.close()


async def _all_tool_calls(store: SessionStore) -> list[tuple[str, str]]:
    async with store.db.execute(
        "SELECT tool_call_id, status FROM tool_calls ORDER BY created_at"
    ) as cur:
        return [(r[0], r[1]) async for r in cur]


async def _all_turns(store: SessionStore) -> list[tuple[str, str | None]]:
    async with store.db.execute(
        "SELECT turn_id, stop_reason FROM turns ORDER BY started_at"
    ) as cur:
        return [(r[0], r[1]) async for r in cur]


async def test_sink_writes_turn_lifecycle(store: SessionStore) -> None:
    sink = SqliteSink(store)
    await sink.write_event(TurnStart(turn_id="t1", session_id="s1", turn_index=0))
    await sink.write_event(TurnEnd(turn_id="t1", session_id="s1", stop_reason="end_turn"))

    turns = await _all_turns(store)
    assert turns == [("t1", "end_turn")]


async def test_sink_writes_tool_call_lifecycle(store: SessionStore) -> None:
    sink = SqliteSink(store)
    await sink.write_event(TurnStart(turn_id="t1", session_id="s1", turn_index=0))
    await sink.write_event(
        ContentBlockStart(
            turn_id="t1",
            session_id="s1",
            index=0,
            content_block=ToolUseBlock(id="call_1", name="write_file", input={"x": 1}),
        )
    )
    # Pre-result: pending.
    assert await _all_tool_calls(store) == [("call_1", "pending")]
    await sink.write_event(
        ToolResult(turn_id="t1", session_id="s1", tool_use_id="call_1", content="ok")
    )
    assert await _all_tool_calls(store) == [("call_1", "completed")]


async def test_sink_lifecycle_is_idempotent_on_replay(store: SessionStore) -> None:
    sink = SqliteSink(store)
    ev = TurnStart(turn_id="t1", session_id="s1", turn_index=0)
    await sink.write_event(ev)
    # Re-emitting (e.g. via WebSocket replay) must not crash on the
    # turns-table unique constraint.
    await sink.write_event(ev)

    turns = await _all_turns(store)
    assert len(turns) == 1


async def test_recover_on_startup_marks_orphan_tool_calls(store: SessionStore) -> None:
    sink = SqliteSink(store)
    await sink.write_event(TurnStart(turn_id="t1", session_id="s1", turn_index=0))
    await sink.write_event(
        ContentBlockStart(
            turn_id="t1",
            session_id="s1",
            index=0,
            content_block=ToolUseBlock(id="orphan_call", name="bash", input={"cmd": "ls"}),
        )
    )
    # Crash here — no ToolResult, no TurnEnd.

    report = await recover_on_startup(store)

    assert report["interrupted_tool_calls"] == 1
    calls = await _all_tool_calls(store)
    assert calls == [("orphan_call", "interrupted")]


async def test_recover_on_startup_finds_interrupted_turns(store: SessionStore) -> None:
    sink = SqliteSink(store)
    await sink.write_event(TurnStart(turn_id="t1", session_id="s1", turn_index=0))
    await sink.write_event(TurnStart(turn_id="t2", session_id="s2", turn_index=0))
    # s1 finishes, s2 crashes mid-turn.
    await sink.write_event(TurnEnd(turn_id="t1", session_id="s1", stop_reason="end_turn"))

    report = await recover_on_startup(store)

    assert report["interrupted_sessions"] == {"s2"}
    # Stamped so a second startup sweep doesn't re-flag it.
    report2 = await recover_on_startup(store)
    assert report2["interrupted_sessions"] == set()


async def test_synthetic_note_injected_for_interrupted_session(store: SessionStore) -> None:
    from collections.abc import AsyncIterator
    from typing import Any

    from pishkar.core.events import Event
    from pishkar.providers.base import ModelProvider, ProviderChunk

    captured_systems: list[str | None] = []

    class CapturingProvider(ModelProvider):
        async def stream(
            self, *, model: str, messages: list[dict[str, Any]],
            tools: list[dict[str, Any]] | None = None,
            system: str | None = None, max_tokens: int | None = None,
            user_id: str | None = None,
        ) -> AsyncIterator[ProviderChunk]:
            captured_systems.append(system)
            yield ProviderChunk(text="ok")
            yield ProviderChunk(stop_reason="stop")

    interrupted = {"s-broken"}
    handler = build_handler(
        provider=CapturingProvider(),
        model="m",
        system="BASE",
        interrupted_sessions=interrupted,
    )
    msg = InboundMessage(user_id="ali", session_id="s-broken", channel="cli", content="hi")

    async def _drain(gen: AsyncIterator[Event]) -> None:
        async for _ in gen:
            pass

    await _drain(handler(msg))

    assert "Recovery notice" in (captured_systems[0] or "")
    # Set is cleared so a follow-up turn doesn't re-inject the note.
    assert "s-broken" not in interrupted

    msg2 = InboundMessage(user_id="ali", session_id="s-broken", channel="cli", content="again")
    await _drain(handler(msg2))
    assert "Recovery notice" not in (captured_systems[1] or "")
