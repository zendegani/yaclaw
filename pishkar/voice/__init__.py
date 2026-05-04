"""Voice I/O — speech-to-text and text-to-speech adapters.

Both interfaces are narrow Protocols so the channel layer stays decoupled
from any specific engine. STT engines live in `stt.py`; TTS in `tts.py`.
"""

from typing import Protocol


class Transcriber(Protocol):
    """Convert spoken audio bytes to text."""

    async def transcribe(self, audio: bytes, *, mime: str = "audio/ogg") -> str: ...


class Synthesizer(Protocol):
    """Convert text into spoken audio bytes (OGG/Opus, suitable for
    Telegram `send_voice`)."""

    async def synthesize(self, text: str) -> bytes: ...


__all__ = ["Synthesizer", "Transcriber"]
