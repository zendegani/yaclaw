"""Session-keyed registry of voice-output callbacks.

The `speak` tool runs in the agent loop, far below the channel layer.
It needs a way to push synthesized audio out without knowing whether
the active channel is Telegram, the Web UI, or something else. Channels
register a `Speaker` callable per session at attach time; the tool
looks one up by session id and delegates.

Empty registry = nothing happens; the tool reports back to the LLM that
voice output isn't configured for the session, and the conversation
continues in text. Same fail-open spirit as the rest of the runtime.
"""

from collections.abc import Awaitable, Callable

Speaker = Callable[[str], Awaitable[None]]

_speakers: dict[str, Speaker] = {}


def register_speaker(session_id: str, speaker: Speaker) -> None:
    _speakers[session_id] = speaker


def unregister_speaker(session_id: str) -> None:
    _speakers.pop(session_id, None)


async def speak(session_id: str, text: str) -> bool:
    """Deliver `text` as voice for `session_id`. Returns False when no
    speaker is registered (channel can't do voice or session is unknown)."""
    speaker = _speakers.get(session_id)
    if speaker is None:
        return False
    await speaker(text)
    return True


def reset() -> None:
    """Test helper — clears the registry."""
    _speakers.clear()


__all__ = ["Speaker", "register_speaker", "reset", "speak", "unregister_speaker"]
