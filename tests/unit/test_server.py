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
    with TestClient(app) as client, client.websocket_connect("/ws/ali/s1") as ws:
        ws.send_text(json.dumps({"content": "hello"}))
        events = _drain_until(ws, "turn_end")

    types = [e["type"] for e in events]
    assert types == ["user_message", "turn_start", "content_block_delta", "turn_end"]
    assert events[0]["content"] == "hello"
    assert events[2]["delta"]["text"] == "echo:hello"


def test_events_are_persisted_and_replayed_on_reconnect(app, tmp_path: Path) -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/ws/ali/s1") as ws:
            ws.send_text(json.dumps({"content": "first"}))
            _drain_until(ws, "turn_end")

        # Reconnect with no last_event_id → full replay before any new events.
        with client.websocket_connect("/ws/ali/s1") as ws:
            replay = _drain_until(ws, "turn_end")
            assert [e["type"] for e in replay] == [
                "user_message", "turn_start", "content_block_delta", "turn_end"
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


def test_handler_exception_yields_turn_end_error_and_keeps_worker_alive(
    tmp_path: Path,
) -> None:
    calls = {"n": 0}

    def flaky(msg: InboundMessage) -> AsyncIterator[Event]:
        async def gen() -> AsyncIterator[Event]:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("provider 429")
            yield TurnStart(
                turn_id=msg.message_id, session_id=msg.session_id, turn_index=0
            )
            yield TurnEnd(
                turn_id=msg.message_id,
                session_id=msg.session_id,
                stop_reason="end_turn",
            )

        return gen()

    store = SessionStore(tmp_path / "sessions.db")
    app = create_app(store=store, handler=flaky)
    with TestClient(app) as client, client.websocket_connect("/ws/ali/s1") as ws:
        ws.send_text(json.dumps({"content": "boom"}))
        first = _drain_until(ws, "turn_end")
        assert first[-1]["stop_reason"] == "error"

        # Worker is still alive — second message gets a normal turn.
        ws.send_text(json.dumps({"content": "again"}))
        second = _drain_until(ws, "turn_end")
        assert second[-1]["stop_reason"] == "end_turn"


async def test_replay_returns_false_on_client_disconnect(tmp_path: Path) -> None:
    from fastapi import WebSocketDisconnect

    from pishkar.server import _replay

    store = SessionStore(tmp_path / "sessions.db")
    await store.open()
    try:
        # Two events to replay; the fake socket dies on the first send.
        for event_id in ("e1", "e2"):
            await store.append_event(
                event_id=event_id,
                type="turn_end",
                payload_json=json.dumps({"event_id": event_id}),
                turn_id=None,
                session_id="s1",
            )

        class _DeadSocket:
            sent = 0

            async def send_text(self, _: str) -> None:
                self.sent += 1
                raise WebSocketDisconnect(code=1001)

        sock = _DeadSocket()
        ok = await _replay(sock, store, "s1", last_event_id=None)
        assert ok is False
        assert sock.sent == 1  # bailed on the first failing send
    finally:
        await store.close()


def test_invalid_inbound_json_is_ignored(app) -> None:
    with TestClient(app) as client, client.websocket_connect("/ws/ali/s1") as ws:
        ws.send_text("not json at all")
        ws.send_text(json.dumps({"content": "real"}))
        events = _drain_until(ws, "turn_end")
    assert any("echo:real" in json.dumps(e) for e in events)


# --- /voice endpoint -------------------------------------------------------


class _StubTranscriber:
    def __init__(self, text: str = "hello there") -> None:
        self.text = text
        self.calls: list[tuple[bytes, str]] = []

    async def transcribe(self, audio: bytes, *, mime: str = "audio/ogg") -> str:
        self.calls.append((audio, mime))
        return self.text


def test_voice_returns_503_when_transcriber_missing(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "s.db")
    app = create_app(store=store, handler=_echo)
    with TestClient(app) as client:
        resp = client.post(
            "/voice/ali/s1", files={"audio": ("a.webm", b"x", "audio/webm")}
        )
    assert resp.status_code == 503


def test_voice_dispatches_transcript_through_websocket(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "s.db")
    transcriber = _StubTranscriber(text="ping")
    app = create_app(store=store, handler=_echo, transcriber=transcriber)
    with TestClient(app) as client, client.websocket_connect("/ws/ali/s1") as ws:
        resp = client.post(
            "/voice/ali/s1",
            files={"audio": ("a.webm", b"oggbytes", "audio/webm")},
            data={"message_id": "msg-123"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"transcript": "ping", "message_id": "msg-123"}
        events = _drain_until(ws, "turn_end")
    types = [e["type"] for e in events]
    assert types == ["user_message", "turn_start", "content_block_delta", "turn_end"]
    assert events[0]["content"] == "ping"
    # Server must echo the client-provided id so the UI can dedupe.
    assert events[0]["message_id"] == "msg-123"
    assert transcriber.calls == [(b"oggbytes", "audio/webm")]


def test_voice_mints_id_when_omitted(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "s.db")
    transcriber = _StubTranscriber(text="ping")
    app = create_app(store=store, handler=_echo, transcriber=transcriber)
    with TestClient(app) as client:
        resp = client.post(
            "/voice/ali/s1",
            files={"audio": ("a.webm", b"oggbytes", "audio/webm")},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["transcript"] == "ping"
    assert body["message_id"] == ""


def test_voice_400_on_empty_transcript(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "s.db")
    transcriber = _StubTranscriber(text="   ")
    app = create_app(store=store, handler=_echo, transcriber=transcriber)
    with TestClient(app) as client:
        resp = client.post(
            "/voice/ali/s1",
            files={"audio": ("a.webm", b"oggbytes", "audio/webm")},
        )
    assert resp.status_code == 400


def test_voice_502_when_transcribe_raises(tmp_path: Path) -> None:
    class _BoomT:
        async def transcribe(self, *_a, **_k) -> str:
            raise RuntimeError("upstream down")

    store = SessionStore(tmp_path / "s.db")
    app = create_app(store=store, handler=_echo, transcriber=_BoomT())
    with TestClient(app) as client:
        resp = client.post(
            "/voice/ali/s1",
            files={"audio": ("a.webm", b"oggbytes", "audio/webm")},
        )
    assert resp.status_code == 502
