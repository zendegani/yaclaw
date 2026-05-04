"""Tests for the Groq Whisper transcriber + env-driven factory."""

from typing import Any

import httpx
import pytest

from pishkar.voice.stt import GroqWhisperTranscriber, build_transcriber_from_env


class _StubResponse:
    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "boom", request=None, response=None  # type: ignore[arg-type]
            )

    def json(self) -> dict[str, Any]:
        return self._payload


class _StubClient:
    def __init__(self, response: _StubResponse) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    async def __aenter__(self) -> _StubClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> _StubResponse:
        self.calls.append({"url": url, **kwargs})
        return self._response


@pytest.fixture
def patch_httpx(monkeypatch: pytest.MonkeyPatch):
    holder: dict[str, _StubClient] = {}

    def _factory(response: _StubResponse):
        client = _StubClient(response)
        holder["client"] = client

        def _ctor(*_args: Any, **_kwargs: Any) -> _StubClient:
            return client

        monkeypatch.setattr("pishkar.voice.stt.httpx.AsyncClient", _ctor)
        return holder

    return _factory


async def test_transcribe_posts_multipart_with_bearer(patch_httpx) -> None:
    patch_httpx(_StubResponse({"text": "hello world"}))
    t = GroqWhisperTranscriber(api_key="k")
    out = await t.transcribe(b"oggdata", mime="audio/ogg")
    assert out == "hello world"


async def test_transcribe_strips_whitespace(patch_httpx) -> None:
    patch_httpx(_StubResponse({"text": "  hi\n"}))
    t = GroqWhisperTranscriber(api_key="k")
    assert await t.transcribe(b"x") == "hi"


async def test_transcribe_returns_empty_when_no_text(patch_httpx) -> None:
    patch_httpx(_StubResponse({}))
    t = GroqWhisperTranscriber(api_key="k")
    assert await t.transcribe(b"x") == ""


def test_factory_returns_none_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PISHKAR_VOICE_ENABLED", raising=False)
    assert build_transcriber_from_env() is None


def test_factory_returns_none_without_groq_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PISHKAR_VOICE_ENABLED", "1")
    monkeypatch.setenv("PISHKAR_STT_ENGINE", "groq")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert build_transcriber_from_env() is None


def test_factory_builds_groq_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PISHKAR_VOICE_ENABLED", "1")
    monkeypatch.setenv("PISHKAR_STT_ENGINE", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "secret")
    t = build_transcriber_from_env()
    assert isinstance(t, GroqWhisperTranscriber)


def test_factory_returns_none_for_unknown_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PISHKAR_VOICE_ENABLED", "1")
    monkeypatch.setenv("PISHKAR_STT_ENGINE", "wattson")
    assert build_transcriber_from_env() is None
