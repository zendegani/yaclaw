"""Tests for the `speak` tool + voice dispatcher."""

import pytest

from pishkar.core.context import current_session_id
from pishkar.tools.speak import speak
from pishkar.voice import dispatcher


@pytest.fixture(autouse=True)
def _reset_dispatcher() -> None:
    dispatcher.reset()


async def test_speak_returns_no_session_when_unset() -> None:
    current_session_id.set("")
    out = await speak("hello")
    assert "No active session" in out


async def test_speak_returns_unconfigured_when_no_speaker() -> None:
    current_session_id.set("s1")
    out = await speak("hello")
    assert "not configured" in out


async def test_speak_invokes_registered_speaker() -> None:
    received: list[str] = []

    async def _speaker(text: str) -> None:
        received.append(text)

    dispatcher.register_speaker("s1", _speaker)
    current_session_id.set("s1")
    out = await speak("hello there")
    assert received == ["hello there"]
    assert out.startswith("Spoke:")


async def test_speak_truncates_preview_in_ack() -> None:
    async def _speaker(_: str) -> None:
        return None

    dispatcher.register_speaker("s1", _speaker)
    current_session_id.set("s1")
    out = await speak("a" * 500)
    assert "…" in out
    assert len(out) < 300


async def test_speak_rejects_blank_input() -> None:
    current_session_id.set("s1")
    assert "Nothing" in await speak("   ")


async def test_dispatcher_unregister_clears_session() -> None:
    async def _speaker(_: str) -> None:
        return None

    dispatcher.register_speaker("s1", _speaker)
    dispatcher.unregister_speaker("s1")
    assert await dispatcher.speak("s1", "hi") is False
