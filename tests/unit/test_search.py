import json
from collections.abc import Callable
from typing import Any

import pytest

from pishkar.tools import search as search_mod


class _FakeHttp:
    def __init__(self, responder: Callable[[dict[str, Any]], str]) -> None:
        self.responder = responder
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        return self.responder(kwargs)


def _install_http(monkeypatch: pytest.MonkeyPatch, responder: Callable) -> _FakeHttp:
    fake = _FakeHttp(responder)
    monkeypatch.setattr(search_mod, "http", fake)
    return fake


def _clear_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("TAVILY_API_KEY", "BRAVE_API_KEY", "PISHKAR_SEARCH_ENGINE"):
        monkeypatch.delenv(k, raising=False)


async def test_explicit_tavily_uses_tavily_api(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_keys(monkeypatch)
    monkeypatch.setenv("TAVILY_API_KEY", "tvly")
    payload = {
        "answer": "42",
        "results": [{"title": "T1", "url": "https://t1", "content": "ct"}],
    }
    fake = _install_http(monkeypatch, lambda _kw: json.dumps(payload))
    out = await search_mod.search(query="meaning", engine="tavily")
    assert "Tavily results for: meaning" in out
    assert "**Answer:** 42" in out
    assert "T1" in out and "https://t1" in out
    [call] = fake.calls
    assert call["url"] == "https://api.tavily.com/search"
    assert call["headers"]["Authorization"] == "Bearer tvly"


async def test_explicit_brave_uses_brave_api(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_keys(monkeypatch)
    monkeypatch.setenv("BRAVE_API_KEY", "brv")
    payload = {"web": {"results": [{"title": "B1", "url": "https://b1", "description": "d"}]}}
    fake = _install_http(monkeypatch, lambda _kw: json.dumps(payload))
    out = await search_mod.search(query="cats", engine="brave")
    assert "Brave results for: cats" in out
    assert "B1" in out
    [call] = fake.calls
    assert call["headers"]["X-Subscription-Token"] == "brv"


async def test_duckduckgo_parses_html(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_keys(monkeypatch)
    html = """
    <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa">Title A</a>
    <a class="result__snippet">snippet A</a>
    <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fb">Title B</a>
    <a class="result__snippet">snippet B</a>
    """
    _install_http(monkeypatch, lambda _kw: html)
    out = await search_mod.search(query="x", engine="duckduckgo", max_results=2)
    assert "DuckDuckGo results for: x" in out
    assert "Title A" in out and "https://example.com/a" in out
    assert "Title B" in out and "https://example.com/b" in out


async def test_falls_back_when_primary_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_keys(monkeypatch)
    monkeypatch.setenv("TAVILY_API_KEY", "tvly")
    monkeypatch.setenv("BRAVE_API_KEY", "brv")

    def responder(kw: dict[str, Any]) -> str:
        if "tavily.com" in kw["url"]:
            raise RuntimeError("tavily down")
        return json.dumps({"web": {"results": [{"title": "fallback", "url": "https://fb"}]}})

    _install_http(monkeypatch, responder)
    out = await search_mod.search(query="q", engine="tavily")
    assert "Brave results for: q" in out
    assert "fallback" in out


async def test_auto_picks_tavily_when_key_present(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_keys(monkeypatch)
    monkeypatch.setenv("TAVILY_API_KEY", "tvly")
    fake = _install_http(monkeypatch, lambda _kw: json.dumps({"results": []}))
    await search_mod.search(query="q", engine="auto")
    assert fake.calls[0]["url"] == "https://api.tavily.com/search"


async def test_auto_falls_back_to_duckduckgo_when_no_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_keys(monkeypatch)
    html = (
        '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fex">T</a>'
        '<a class="result__snippet">s</a>'
    )
    fake = _install_http(monkeypatch, lambda _kw: html)
    out = await search_mod.search(query="q", engine="auto")
    assert "duckduckgo" in fake.calls[0]["url"]
    assert "DuckDuckGo results for: q" in out


async def test_env_override_picks_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_keys(monkeypatch)
    monkeypatch.setenv("TAVILY_API_KEY", "tvly")
    monkeypatch.setenv("BRAVE_API_KEY", "brv")
    monkeypatch.setenv("PISHKAR_SEARCH_ENGINE", "brave")
    fake = _install_http(
        monkeypatch,
        lambda _kw: json.dumps({"web": {"results": []}}),
    )
    await search_mod.search(query="q")
    assert "search.brave.com" in fake.calls[0]["url"]


async def test_all_engines_failing_returns_error_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_keys(monkeypatch)

    def responder(_kw: dict[str, Any]) -> str:
        raise RuntimeError("nope")

    _install_http(monkeypatch, responder)
    out = await search_mod.search(query="q", engine="auto")
    assert out.startswith("[search failed across all engines]")
    assert "duckduckgo" in out and "tavily" in out and "brave" in out
