from typing import Any

import pytest

from pishkar.tools import read_url as read_url_mod


class _Recorder:
    def __init__(self, response: str = "# page\n") -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        return self.response


@pytest.fixture
def fake_http(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    rec = _Recorder()
    monkeypatch.setattr(read_url_mod, "http", rec)
    return rec


async def test_read_url_uses_jina_prefix(
    fake_http: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("JINA_API_KEY", raising=False)
    out = await read_url_mod.read_url("https://example.com/page")
    assert out == "# page\n"
    [call] = fake_http.calls
    assert call["url"] == "https://r.jina.ai/https://example.com/page"
    assert call["headers"]["Accept"] == "text/markdown"
    assert "Authorization" not in call["headers"]


async def test_read_url_sends_bearer_when_key_set(
    fake_http: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JINA_API_KEY", "secret")
    await read_url_mod.read_url("https://example.com/")
    [call] = fake_http.calls
    assert call["headers"]["Authorization"] == "Bearer secret"


async def test_read_url_quotes_unsafe_chars(
    fake_http: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("JINA_API_KEY", raising=False)
    await read_url_mod.read_url("https://example.com/path with space")
    [call] = fake_http.calls
    assert "%20" in call["url"]
