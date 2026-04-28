import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pishkar.core.events import (
    ContentBlockDelta,
    Event,
    TextDelta,
    TurnEnd,
    TurnStart,
)
from pishkar.core.messages import InboundMessage
from pishkar.server import create_app
from pishkar.workspace.store import SessionStore


def _echo(msg: InboundMessage) -> AsyncIterator[Event]:
    async def gen() -> AsyncIterator[Event]:
        yield TurnStart(turn_id=msg.message_id, session_id=msg.session_id, turn_index=0)
        yield ContentBlockDelta(
            turn_id=msg.message_id,
            session_id=msg.session_id,
            index=0,
            delta=TextDelta(text=f"echo:{msg.content}"),
        )
        yield TurnEnd(turn_id=msg.message_id, session_id=msg.session_id, stop_reason="end_turn")
    return gen()


@pytest.fixture
def app(tmp_path: Path):
    store = SessionStore(tmp_path / "sessions.db")
    return create_app(store=store, handler=_echo)


def _drain_until(ws, stop_type: str, limit: int = 20) -> list[dict]:
    received = []
    for _ in range(limit):
        text = ws.receive_text()
        payload = json.loads(text)
        received.append(payload)
        if payload.get("type") == stop_type:
            return received
    raise AssertionError(f"never saw {stop_type}; got {received}")


def test_websocket_round_trip(app) -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/ws/ali/s1") as ws:
            ws.send_text(json.dumps({"content": "hello"}))
            events = _drain_until(ws, "turn_end")

    types = [e["type"] for e in events]
    assert types == ["turn_start", "content_block_delta", "turn_end"]
    assert events[1]["delta"]["text"] == "echo:hello"


def test_events_are_persisted_and_replayed_on_reconnect(app, tmp_path: Path) -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/ws/ali/s1") as ws:
            ws.send_text(json.dumps({"content": "first"}))
            _drain_until(ws, "turn_end")

        # Reconnect with no last_event_id → full replay before any new events.
        with client.websocket_connect("/ws/ali/s1") as ws:
            replay = _drain_until(ws, "turn_end")
            assert [e["type"] for e in replay] == [
                "turn_start", "content_block_delta", "turn_end"
            ]


def test_reconnect_with_last_event_id_skips_seen(app) -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/ws/ali/s1") as ws:
            ws.send_text(json.dumps({"content": "one"}))
            first = _drain_until(ws, "turn_end")

        # Reconnect after the last event of the first turn.
        last_id = first[-1]["event_id"]
        with client.websocket_connect(f"/ws/ali/s1?last_event_id={last_id}") as ws:
            ws.send_text(json.dumps({"content": "two"}))
            second = _drain_until(ws, "turn_end")

    # No replay of first turn — only the events from the second turn.
    assert all("echo:one" not in json.dumps(e) for e in second)
    assert any("echo:two" in json.dumps(e) for e in second)


def test_session_isolation_across_sockets(app) -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/ws/ali/s1") as a:
            a.send_text(json.dumps({"content": "for-s1"}))
            _drain_until(a, "turn_end")

        with client.websocket_connect("/ws/ali/s2") as b:
            # s2 must not see s1's replayed events.
            b.send_text(json.dumps({"content": "for-s2"}))
            events = _drain_until(b, "turn_end")
            assert all("for-s1" not in json.dumps(e) for e in events)


def test_invalid_inbound_json_is_ignored(app) -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/ws/ali/s1") as ws:
            ws.send_text("not json at all")
            ws.send_text(json.dumps({"content": "real"}))
            events = _drain_until(ws, "turn_end")
    assert any("echo:real" in json.dumps(e) for e in events)
