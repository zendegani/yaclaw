import asyncio
import io
import json
import os

import pytest

from pishkar.channels.base import Channel
from pishkar.channels.cli import CLIChannel
from pishkar.channels.ws import WebSocketChannel, WebSocketDisconnect
from pishkar.core.events import (
    ContentBlockDelta,
    TextDelta,
    ToolResult,
    TurnEnd,
    TurnStart,
)

# ---- WebSocketChannel ------------------------------------------------------


class FakeSocket:
    def __init__(self, incoming: list[str]) -> None:
        self._incoming = list(incoming)
        self.sent: list[str] = []
        self.closed = False

    async def receive_text(self) -> str:
        if not self._incoming:
            raise WebSocketDisconnect
        return self._incoming.pop(0)

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        self.closed = True


def _msg(content: str, **extra: object) -> str:
    return json.dumps({"content": content, **extra})


async def test_ws_channel_satisfies_protocol() -> None:
    ch = WebSocketChannel(FakeSocket([]), user_id="ali", session_id="s1")
    assert isinstance(ch, Channel)


async def test_ws_inbound_parses_and_fills_defaults() -> None:
    sock = FakeSocket([_msg("hello"), _msg("world")])
    ch = WebSocketChannel(sock, user_id="ali", session_id="s1")
    msgs = [m async for m in ch.inbound()]
    assert [m.content for m in msgs] == ["hello", "world"]
    assert all(m.user_id == "ali" and m.session_id == "s1" for m in msgs)
    assert all(m.channel == "ws" for m in msgs)


async def test_ws_inbound_skips_invalid_json() -> None:
    sock = FakeSocket(["not json", _msg("ok")])
    ch = WebSocketChannel(sock, user_id="ali", session_id="s1")
    msgs = [m async for m in ch.inbound()]
    assert [m.content for m in msgs] == ["ok"]


async def test_ws_inbound_skips_invalid_payload() -> None:
    sock = FakeSocket([json.dumps({"no_content": True}), _msg("ok")])
    ch = WebSocketChannel(sock, user_id="ali", session_id="s1")
    msgs = [m async for m in ch.inbound()]
    assert [m.content for m in msgs] == ["ok"]


async def test_ws_inbound_terminates_on_disconnect() -> None:
    sock = FakeSocket([_msg("hi")])
    ch = WebSocketChannel(sock, user_id="ali", session_id="s1")
    msgs = [m async for m in ch.inbound()]
    assert len(msgs) == 1


async def test_ws_send_event_serializes_json() -> None:
    sock = FakeSocket([])
    ch = WebSocketChannel(sock, user_id="ali", session_id="s1")
    await ch.send_event(TurnStart(turn_id="t", session_id="s1", turn_index=0))
    assert len(sock.sent) == 1
    payload = json.loads(sock.sent[0])
    assert payload["type"] == "turn_start"
    assert payload["turn_id"] == "t"


async def test_ws_close_is_idempotent_and_silences_sends() -> None:
    sock = FakeSocket([])
    ch = WebSocketChannel(sock, user_id="ali", session_id="s1")
    await ch.close()
    await ch.close()
    await ch.send_event(TurnStart(turn_id="t", session_id="s1", turn_index=0))
    assert sock.sent == []
    assert sock.closed is True


async def test_ws_custom_disconnect_exception() -> None:
    class CustomDisconnect(Exception):
        pass

    class S:
        async def receive_text(self) -> str:
            raise CustomDisconnect

        async def send_text(self, _: str) -> None: ...
        async def close(self) -> None: ...

    ch = WebSocketChannel(
        S(), user_id="u", session_id="s", disconnect_exc=CustomDisconnect
    )
    assert [m async for m in ch.inbound()] == []


# ---- CLIChannel ------------------------------------------------------------


async def test_cli_channel_satisfies_protocol() -> None:
    ch = CLIChannel(user_id="ali", session_id="s")
    assert isinstance(ch, Channel)


async def test_cli_inbound_reads_stdin_lines() -> None:
    stdin = io.StringIO("hello\nworld\n")
    ch = CLIChannel(user_id="ali", session_id="s", stdin=stdin, stdout=io.StringIO())
    msgs = [m async for m in ch.inbound()]
    assert [m.content for m in msgs] == ["hello", "world"]


async def test_cli_inbound_skips_blank_lines() -> None:
    stdin = io.StringIO("hi\n\n\nthere\n")
    ch = CLIChannel(user_id="ali", session_id="s", stdin=stdin, stdout=io.StringIO())
    msgs = [m async for m in ch.inbound()]
    assert [m.content for m in msgs] == ["hi", "there"]


async def test_cli_send_event_renders_text_delta() -> None:
    out = io.StringIO()
    ch = CLIChannel(user_id="ali", session_id="s", stdin=io.StringIO(), stdout=out)
    await ch.send_event(
        ContentBlockDelta(
            turn_id="t", session_id="s", index=0, delta=TextDelta(text="hi")
        )
    )
    assert out.getvalue() == "hi"


async def test_cli_send_event_renders_tool_and_turn_end() -> None:
    out = io.StringIO()
    ch = CLIChannel(user_id="ali", session_id="s", stdin=io.StringIO(), stdout=out)
    await ch.send_event(
        ToolResult(turn_id="t", session_id="s", tool_use_id="x", content="ok")
    )
    await ch.send_event(
        ToolResult(turn_id="t", session_id="s", tool_use_id="y",
                   content="boom", is_error=True)
    )
    await ch.send_event(TurnEnd(turn_id="t", session_id="s", stop_reason="end_turn"))
    text = out.getvalue()
    assert "[tool] ok" in text
    assert "[tool error] boom" in text
    assert "[end: end_turn]" in text


async def test_cli_send_event_ignores_unrendered_events() -> None:
    out = io.StringIO()
    ch = CLIChannel(user_id="ali", session_id="s", stdin=io.StringIO(), stdout=out)
    await ch.send_event(TurnStart(turn_id="t", session_id="s", turn_index=0))
    assert out.getvalue() == ""


async def test_cli_close_stops_inbound_loop() -> None:
    # A blocking stdin we control: pipe + slow drip.
    rfd, wfd = os.pipe()
    stdin = os.fdopen(rfd, "r")
    ch = CLIChannel(user_id="ali", session_id="s", stdin=stdin, stdout=io.StringIO())

    async def drain() -> list[str]:
        return [m.content async for m in ch.inbound()]

    task = asyncio.create_task(drain())
    os.write(wfd, b"hi\n")
    await asyncio.sleep(0.05)
    await ch.close()
    os.close(wfd)
    msgs = await asyncio.wait_for(task, timeout=1.0)
    assert msgs == ["hi"]
    stdin.close()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
