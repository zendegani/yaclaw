from pathlib import Path
from uuid import uuid4

import pytest

from pishkar.core.messages import InboundMessage, OutboundMessage
from pishkar.workspace.store import SessionStore, args_hash


@pytest.fixture
async def store(tmp_path: Path):
    s = SessionStore(tmp_path / "sessions.db")
    await s.open()
    try:
        yield s
    finally:
        await s.close()


def _inbound(session_id: str, content: str = "hi", user_id: str = "ali") -> InboundMessage:
    return InboundMessage(
        user_id=user_id, session_id=session_id, channel="cli", content=content
    )


def _outbound(session_id: str, content: str = "hello", user_id: str = "ali") -> OutboundMessage:
    return OutboundMessage(
        user_id=user_id, session_id=session_id, channel="cli", content=content
    )


def test_args_hash_is_canonical() -> None:
    assert args_hash({"a": 1, "b": 2}) == args_hash({"b": 2, "a": 1})
    assert args_hash({"a": 1}) != args_hash({"a": 2})


async def test_create_and_get_session(store: SessionStore) -> None:
    s = await store.create_session("ali")
    fetched = await store.get_session(s.session_id)
    assert fetched is not None
    assert fetched.user_id == "ali"
    assert fetched.session_id == s.session_id


async def test_get_missing_session_returns_none(store: SessionStore) -> None:
    assert await store.get_session("nope") is None


async def test_inbound_queue_round_trip(store: SessionStore) -> None:
    s = await store.create_session("ali")
    m1 = _inbound(s.session_id, "first")
    m2 = _inbound(s.session_id, "second")
    await store.enqueue_inbound(m1)
    await store.enqueue_inbound(m2)

    pending = await store.fetch_undelivered_inbound()
    assert [m.content for m in pending] == ["first", "second"]

    await store.mark_delivered(m1.message_id)
    pending = await store.fetch_undelivered_inbound()
    assert [m.content for m in pending] == ["second"]


async def test_session_history_orders_inbound_and_outbound(store: SessionStore) -> None:
    s = await store.create_session("ali")
    await store.enqueue_inbound(_inbound(s.session_id, "in1"))
    await store.record_outbound(_outbound(s.session_id, "out1"))
    history = await store.session_history(s.session_id)
    assert [(h["direction"], h["content"]) for h in history] == [
        ("inbound", "in1"),
        ("outbound", "out1"),
    ]


async def test_turn_lifecycle_and_interrupted_detection(store: SessionStore) -> None:
    s = await store.create_session("ali")
    finished = str(uuid4())
    interrupted = str(uuid4())
    await store.start_turn(finished, s.session_id)
    await store.end_turn(finished, "end_turn")
    await store.start_turn(interrupted, s.session_id)

    assert await store.find_interrupted_turns(s.session_id) == [interrupted]
    assert await store.find_interrupted_turns() == [interrupted]


async def test_complete_tool_call_writes_result_and_flips_status(store: SessionStore) -> None:
    s = await store.create_session("ali")
    turn_id = str(uuid4())
    await store.start_turn(turn_id, s.session_id)
    call_id = str(uuid4())
    await store.record_tool_call(call_id, turn_id, "bash", {"cmd": "ls"})

    await store.complete_tool_call(call_id, "out", is_error=False)

    async with store.db.execute(
        "SELECT status FROM tool_calls WHERE tool_call_id = ?", (call_id,)
    ) as cur:
        assert (await cur.fetchone())[0] == "completed"
    async with store.db.execute(
        "SELECT content, is_error FROM tool_results WHERE tool_call_id = ?", (call_id,)
    ) as cur:
        row = await cur.fetchone()
    assert row == ("out", 0)


async def test_complete_tool_call_error_status(store: SessionStore) -> None:
    s = await store.create_session("ali")
    turn_id = str(uuid4())
    await store.start_turn(turn_id, s.session_id)
    call_id = str(uuid4())
    await store.record_tool_call(call_id, turn_id, "bash", {})
    await store.complete_tool_call(call_id, "boom", is_error=True)
    async with store.db.execute(
        "SELECT status FROM tool_calls WHERE tool_call_id = ?", (call_id,)
    ) as cur:
        assert (await cur.fetchone())[0] == "error"


async def test_mark_orphan_tool_calls_interrupted(store: SessionStore) -> None:
    s = await store.create_session("ali")
    turn_id = str(uuid4())
    await store.start_turn(turn_id, s.session_id)
    done = str(uuid4())
    orphan = str(uuid4())
    await store.record_tool_call(done, turn_id, "bash", {"x": 1})
    await store.complete_tool_call(done, "ok")
    await store.record_tool_call(orphan, turn_id, "bash", {"x": 2})

    flipped = await store.mark_orphan_tool_calls_interrupted()
    assert flipped == 1
    async with store.db.execute(
        "SELECT status FROM tool_calls WHERE tool_call_id = ?", (orphan,)
    ) as cur:
        assert (await cur.fetchone())[0] == "interrupted"


async def test_record_governance_decision(store: SessionStore) -> None:
    await store.record_governance_decision(
        user_id="ali", tool_name="bash", decision="allow", scope="once"
    )
    async with store.db.execute(
        "SELECT user_id, tool_name, decision, scope FROM governance_decisions"
    ) as cur:
        rows = await cur.fetchall()
    assert rows == [("ali", "bash", "allow", "once")]


async def test_token_spend_aggregation(store: SessionStore) -> None:
    await store.record_token_spend("ali", "claude-opus", 10, 5)
    await store.record_token_spend("ali", "claude-opus", 3, 7)
    await store.record_token_spend("guest", "claude-opus", 100, 100)

    inp, out = await store.tokens_spent_since("ali", "1970-01-01T00:00:00+00:00")
    assert (inp, out) == (13, 12)

    inp, out = await store.tokens_spent_since("ali", "9999-01-01T00:00:00+00:00")
    assert (inp, out) == (0, 0)


async def test_reopen_persists_data(tmp_path: Path) -> None:
    db = tmp_path / "sessions.db"
    async with SessionStore(db) as s:
        sess = await s.create_session("ali")
    async with SessionStore(db) as s:
        assert (await s.get_session(sess.session_id)) is not None
