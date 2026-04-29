from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from pishkar.channels.telegram import TelegramBotRunner, TelegramChannel
from pishkar.core.events import (
    ApprovalRequest,
    ContentBlockDelta,
    Event,
    TextDelta,
    TurnEnd,
    TurnStart,
)
from pishkar.core.messages import InboundMessage
from pishkar.gateway.approval_router import ApprovalRouter
from pishkar.gateway.gateway import Gateway
from pishkar.tools.approval_gate import ApprovalDecision
from pishkar.workspace.store import SessionStore


# --- TelegramChannel: outbound formatting ---------------------------------


async def _drain(channel: TelegramChannel, events: list[Event]) -> None:
    for ev in events:
        await channel.send_event(ev)


async def test_channel_buffers_text_and_sends_on_turn_end() -> None:
    send = AsyncMock()
    chan = TelegramChannel(chat_id=42, send_message=send)
    turn = str(uuid4())
    sid = "s1"
    await _drain(chan, [
        TurnStart(turn_id=turn, session_id=sid, turn_index=0),
        ContentBlockDelta(
            turn_id=turn, session_id=sid, index=0, delta=TextDelta(text="hello ")
        ),
        ContentBlockDelta(
            turn_id=turn, session_id=sid, index=0, delta=TextDelta(text="world")
        ),
        TurnEnd(turn_id=turn, session_id=sid, stop_reason="end_turn"),
    ])
    send.assert_awaited_once()
    kwargs = send.await_args.kwargs
    assert kwargs == {"chat_id": 42, "text": "hello world"}


async def test_channel_skips_send_when_no_text() -> None:
    send = AsyncMock()
    chan = TelegramChannel(chat_id=1, send_message=send)
    turn = str(uuid4())
    await _drain(chan, [
        TurnStart(turn_id=turn, session_id="s1", turn_index=0),
        TurnEnd(turn_id=turn, session_id="s1", stop_reason="end_turn"),
    ])
    send.assert_not_awaited()


async def test_channel_renders_error_turn_end() -> None:
    send = AsyncMock()
    chan = TelegramChannel(chat_id=7, send_message=send)
    turn = str(uuid4())
    await chan.send_event(
        TurnEnd(turn_id=turn, session_id="s1", stop_reason="error")
    )
    send.assert_awaited_once()
    assert "wrong" in send.await_args.kwargs["text"].lower()


async def test_channel_chunks_long_messages() -> None:
    send = AsyncMock()
    chan = TelegramChannel(chat_id=1, send_message=send)
    turn = str(uuid4())
    sid = "s1"
    await _drain(chan, [
        ContentBlockDelta(
            turn_id=turn, session_id=sid, index=0, delta=TextDelta(text="x" * 9000)
        ),
        TurnEnd(turn_id=turn, session_id=sid, stop_reason="end_turn"),
    ])
    # 9000 / 4000 = 2 full + 1 partial = 3 sends
    assert send.await_count == 3


async def test_channel_renders_approval_with_inline_keyboard() -> None:
    send = AsyncMock()
    chan = TelegramChannel(chat_id=99, send_message=send)
    await chan.send_event(
        ApprovalRequest(
            turn_id="t1",
            session_id="s1",
            request_id="req-abc",
            tool_name="write_file",
            input={"path": "/tmp/x"},
        )
    )
    send.assert_awaited_once()
    kwargs = send.await_args.kwargs
    assert kwargs["chat_id"] == 99
    assert "write_file" in kwargs["text"]
    keyboard = kwargs["reply_markup"].inline_keyboard
    assert [b.callback_data for b in keyboard[0]] == [
        "allow_once:req-abc",
        "deny:req-abc",
    ]


async def test_channel_after_close_drops_events() -> None:
    send = AsyncMock()
    chan = TelegramChannel(chat_id=1, send_message=send)
    await chan.close()
    await chan.send_event(
        TurnEnd(turn_id="t", session_id="s", stop_reason="end_turn")
    )
    send.assert_not_awaited()


async def test_channel_inbound_is_empty() -> None:
    chan = TelegramChannel(chat_id=1, send_message=AsyncMock())
    msgs: list[InboundMessage] = []
    async for m in chan.inbound():
        msgs.append(m)
    assert msgs == []


async def test_channel_swallows_send_failures() -> None:
    send = AsyncMock(side_effect=RuntimeError("network down"))
    chan = TelegramChannel(chat_id=1, send_message=send)
    # Must not raise.
    await chan.send_event(
        TurnEnd(turn_id="t", session_id="s", stop_reason="end_turn")
    )


# --- TelegramBotRunner: session lifecycle (no real PTB) -------------------


@pytest.fixture
async def gateway(tmp_path) -> AsyncIterator[Gateway]:
    store = SessionStore(tmp_path / "s.db")
    await store.open()

    async def handler(msg: InboundMessage):
        async def gen():
            yield TurnEnd(
                turn_id="t", session_id=msg.session_id, stop_reason="end_turn"
            )

        return gen()

    gw = Gateway(store=store, handler=handler)
    await gw.start()
    try:
        yield gw
    finally:
        await gw.stop()
        await store.close()


def _make_runner(gw: Gateway, *, owner_id: int = 100) -> TelegramBotRunner:
    runner = TelegramBotRunner(
        token="x",
        owner_id=owner_id,
        user_id="ali",
        gateway=gw,
        approval_router=ApprovalRouter(),
    )
    # Stub the Application so _open_session can build a channel without
    # actually talking to Telegram.
    fake_app = MagicMock()
    fake_app.bot.send_message = AsyncMock()
    runner._app = fake_app  # type: ignore[attr-defined]
    return runner


async def test_runner_opens_one_session_per_chat(gateway: Gateway) -> None:
    runner = _make_runner(gateway)
    sid_a = runner._ensure_channel(chat_id=1)
    sid_a_again = runner._ensure_channel(chat_id=1)
    sid_b = runner._ensure_channel(chat_id=2)
    assert sid_a == sid_a_again
    assert sid_a != sid_b
    # Each chat got one channel registered with the gateway.
    assert gateway._channels[sid_a] == [runner._channels[1]]
    assert gateway._channels[sid_b] == [runner._channels[2]]


async def test_runner_rotates_session_on_new(gateway: Gateway) -> None:
    runner = _make_runner(gateway)
    old_sid = runner._ensure_channel(chat_id=1)
    old_channel = runner._channels[1]
    await runner._rotate_session(chat_id=1)
    new_sid = runner._sessions[1]
    new_channel = runner._channels[1]
    assert new_sid != old_sid
    assert new_channel is not old_channel
    # Old channel detached, new one registered.
    assert old_channel not in gateway._channels.get(old_sid, [])
    assert new_channel in gateway._channels[new_sid]


async def test_runner_callback_resolves_approval(gateway: Gateway) -> None:
    import asyncio

    runner = _make_runner(gateway)
    sid = runner._ensure_channel(chat_id=1)
    router = runner._approval_router
    assert router is not None

    waiter = asyncio.create_task(
        router.request(
            session_id=sid, turn_id="t1", tool_name="write_file", args={}
        )
    )
    # Yield until the request_id is registered in `_pending`.
    for _ in range(10):
        await asyncio.sleep(0)
        keys = [k for k in router._pending if k[0] == sid]
        if keys:
            break
    assert keys, "approval request never landed in _pending"
    request_id = keys[0][1]

    update = MagicMock()
    update.effective_user.id = 100
    update.callback_query.data = f"allow_once:{request_id}"
    update.callback_query.message.chat_id = 1
    update.callback_query.message.text = "approval question"
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.callback_query.edit_message_reply_markup = AsyncMock()
    await runner._on_callback(update, MagicMock())

    decision = await waiter
    assert decision == ApprovalDecision.ALLOW_ONCE


async def test_runner_ignores_non_owner(gateway: Gateway) -> None:
    runner = _make_runner(gateway, owner_id=100)
    update = MagicMock()
    update.effective_user.id = 999  # not owner
    update.message.text = "hi"
    update.message.chat_id = 1
    await runner._on_message(update, MagicMock())
    # No session ever opened.
    assert runner._sessions == {}


def test_factory_disabled_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    from pishkar.server import _telegram_factory_from_env

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_OWNER_ID", raising=False)
    assert _telegram_factory_from_env(user_id="ali") is None


def test_factory_disabled_when_owner_not_int(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pishkar.server import _telegram_factory_from_env

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_OWNER_ID", "notanumber")
    assert _telegram_factory_from_env(user_id="ali") is None


def test_factory_returns_runner_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pishkar.server import _telegram_factory_from_env

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_OWNER_ID", "12345")
    factory = _telegram_factory_from_env(user_id="ali")
    assert factory is not None
    fake_gw: Any = MagicMock()
    runners = factory(fake_gw, None)
    assert len(runners) == 1
    assert isinstance(runners[0], TelegramBotRunner)
