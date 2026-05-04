"""Text-to-speech engines.

`PiperSynthesizer` shells out to the `piper` binary (offline, fast, runs
fine on a Pi 5) and pipes stdout WAV through `ffmpeg` to produce OGG/Opus
bytes that Telegram accepts as a voice note. Subprocess invocation uses
`asyncio.create_subprocess_exec` (no shell), so user-controlled text
goes only through stdin and cannot inject arguments.

The factory `build_synthesizer_from_env()` returns `None` when TTS is
not configured. Telegram falls back to a plain text reply in that case.
"""

import asyncio
import logging
import os

from pishkar.voice import Synthesizer

logger = logging.getLogger(__name__)

_PROC_TIMEOUT_S = 30.0


class PiperSynthesizer:
    """Local Piper TTS → OGG/Opus via piper | ffmpeg."""

    def __init__(
        self,
        *,
        piper_bin: str,
        voice_model: str,
        ffmpeg_bin: str = "ffmpeg",
    ) -> None:
        self._piper = piper_bin
        self._voice = voice_model
        self._ffmpeg = ffmpeg_bin

    async def synthesize(self, text: str) -> bytes:
        wav = await self._run_piper(text)
        return await self._wav_to_ogg(wav)

    async def _run_piper(self, text: str) -> bytes:
        proc = await asyncio.create_subprocess_exec(
            self._piper,
            "--model", self._voice,
            "--output_file", "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(text.encode("utf-8")), timeout=_PROC_TIMEOUT_S
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"piper failed (rc={proc.returncode}): "
                f"{stderr.decode('utf-8', errors='replace')[:500]}"
            )
        return stdout

    async def _wav_to_ogg(self, wav: bytes) -> bytes:
        proc = await asyncio.create_subprocess_exec(
            self._ffmpeg,
            "-loglevel", "error",
            "-i", "pipe:0",
            "-c:a", "libopus",
            "-b:a", "32k",
            "-ac", "1",
            "-f", "ogg",
            "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(wav), timeout=_PROC_TIMEOUT_S
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"ffmpeg failed (rc={proc.returncode}): "
                f"{stderr.decode('utf-8', errors='replace')[:500]}"
            )
        return stdout


def build_synthesizer_from_env() -> Synthesizer | None:
    """Construct a TTS engine from env, or return `None` to skip voice
    replies. Voice-in still works when this returns `None`."""
    if os.environ.get("PISHKAR_VOICE_ENABLED", "").lower() not in {
        "1", "true", "yes", "on",
    }:
        return None
    engine = os.environ.get("PISHKAR_TTS_ENGINE", "").lower()
    if not engine:
        return None
    if engine == "piper":
        piper_bin = os.environ.get("PISHKAR_PIPER_BIN", "piper")
        voice = os.environ.get("PISHKAR_PIPER_VOICE")
        if not voice:
            logger.warning(
                "PISHKAR_TTS_ENGINE=piper but PISHKAR_PIPER_VOICE is not "
                "set; voice replies disabled."
            )
            return None
        ffmpeg_bin = os.environ.get("PISHKAR_FFMPEG_BIN", "ffmpeg")
        return PiperSynthesizer(
            piper_bin=piper_bin, voice_model=voice, ffmpeg_bin=ffmpeg_bin
        )
    logger.warning(
        "Unknown PISHKAR_TTS_ENGINE=%r; voice replies disabled.", engine
    )
    return None


__all__ = ["PiperSynthesizer", "build_synthesizer_from_env"]
