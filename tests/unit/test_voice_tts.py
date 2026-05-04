"""Tests for Piper TTS factory + subprocess-based synth.

The actual subprocess invocation is monkeypatched — running the real
`piper` binary in CI is overkill and adds an external dep to the test
suite.
"""

from typing import Any

import pytest

from pishkar.voice.tts import PiperSynthesizer, build_synthesizer_from_env


class _FakeProc:
    def __init__(self, stdout: bytes, stderr: bytes = b"", returncode: int = 0) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.communicate_calls: list[bytes | None] = []

    async def communicate(self, stdin: bytes | None = None) -> tuple[bytes, bytes]:
        self.communicate_calls.append(stdin)
        return self._stdout, self._stderr


def test_factory_disabled_when_voice_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PISHKAR_VOICE_ENABLED", raising=False)
    assert build_synthesizer_from_env() is None


def test_factory_returns_none_without_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PISHKAR_VOICE_ENABLED", "1")
    monkeypatch.delenv("PISHKAR_TTS_ENGINE", raising=False)
    assert build_synthesizer_from_env() is None


def test_factory_needs_voice_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PISHKAR_VOICE_ENABLED", "1")
    monkeypatch.setenv("PISHKAR_TTS_ENGINE", "piper")
    monkeypatch.delenv("PISHKAR_PIPER_VOICE", raising=False)
    assert build_synthesizer_from_env() is None


def test_factory_builds_piper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PISHKAR_VOICE_ENABLED", "1")
    monkeypatch.setenv("PISHKAR_TTS_ENGINE", "piper")
    monkeypatch.setenv("PISHKAR_PIPER_VOICE", "/voices/en.onnx")
    s = build_synthesizer_from_env()
    assert isinstance(s, PiperSynthesizer)


async def test_synthesize_pipes_text_through_piper_then_ffmpeg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    procs: list[_FakeProc] = [
        _FakeProc(stdout=b"WAVDATA"),       # piper
        _FakeProc(stdout=b"OGGDATA"),       # ffmpeg
    ]
    proc_iter = iter(procs)

    async def fake_spawn(*_args: Any, **_kwargs: Any) -> _FakeProc:
        return next(proc_iter)

    monkeypatch.setattr(
        "pishkar.voice.tts.asyncio.create_subprocess_exec", fake_spawn
    )
    s = PiperSynthesizer(piper_bin="piper", voice_model="/v.onnx")
    out = await s.synthesize("hello there")
    assert out == b"OGGDATA"
    # Piper got the text on stdin, ffmpeg got the WAV bytes.
    assert procs[0].communicate_calls == [b"hello there"]
    assert procs[1].communicate_calls == [b"WAVDATA"]


async def test_synthesize_raises_when_piper_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_spawn(*_args: Any, **_kwargs: Any) -> _FakeProc:
        return _FakeProc(stdout=b"", stderr=b"missing model", returncode=1)

    monkeypatch.setattr(
        "pishkar.voice.tts.asyncio.create_subprocess_exec", fake_spawn
    )
    s = PiperSynthesizer(piper_bin="piper", voice_model="/v.onnx")
    with pytest.raises(RuntimeError, match="piper failed"):
        await s.synthesize("x")
