"""Speech-to-text engines.

`GroqWhisperTranscriber` calls Groq's `audio/transcriptions` endpoint —
free tier, very fast (Whisper large v3, multilingual). Reuses
`GROQ_API_KEY` so no extra env var is needed.

The factory `build_transcriber_from_env()` returns `None` when STT is
not configured, so the channel can fall through to text-only quietly.
"""

import logging
import os

import httpx

from pishkar.voice import Transcriber

logger = logging.getLogger(__name__)

_GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
_DEFAULT_MODEL = "whisper-large-v3"
_TIMEOUT_S = 60.0


class GroqWhisperTranscriber:
    """Whisper-large-v3 via Groq's OpenAI-compatible audio endpoint."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = _DEFAULT_MODEL,
        url: str = _GROQ_URL,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._url = url

    async def transcribe(self, audio: bytes, *, mime: str = "audio/ogg") -> str:
        files = {"file": ("audio.ogg", audio, mime)}
        data = {"model": self._model}
        headers = {"Authorization": f"Bearer {self._api_key}"}
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.post(
                self._url, headers=headers, data=data, files=files
            )
            resp.raise_for_status()
            payload = resp.json()
        return str(payload.get("text", "")).strip()


def build_transcriber_from_env() -> Transcriber | None:
    """Construct an STT engine from `PISHKAR_STT_ENGINE` + provider keys.

    Returns `None` when voice is disabled or the engine can't be built —
    the caller falls back to text-only."""
    if os.environ.get("PISHKAR_VOICE_ENABLED", "").lower() not in {
        "1", "true", "yes", "on",
    }:
        return None
    engine = os.environ.get("PISHKAR_STT_ENGINE", "groq").lower()
    if engine == "groq":
        key = os.environ.get("GROQ_API_KEY")
        if not key:
            logger.warning(
                "PISHKAR_STT_ENGINE=groq but GROQ_API_KEY is not set; "
                "voice input disabled."
            )
            return None
        return GroqWhisperTranscriber(api_key=key)
    logger.warning(
        "Unknown PISHKAR_STT_ENGINE=%r; voice input disabled.", engine
    )
    return None


__all__ = ["GroqWhisperTranscriber", "build_transcriber_from_env"]
