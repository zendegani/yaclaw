"""`search` tool — web search across Tavily, Brave, and DuckDuckGo.

The model picks an engine via the `engine` argument or lets it default to
`PISHKAR_SEARCH_ENGINE` (or auto-pick based on which API keys are set).
On API errors / rate limits, the engine list is walked in fallback order
so a transient Tavily 429 still returns Brave or DuckDuckGo results.

Tavily and Brave use their official APIs (keys via env). DuckDuckGo uses
its public HTML endpoint — keyless but rate-limited; fine as a fallback
or for prototyping.
"""

import json as _json
import os
import re
import urllib.parse
from typing import Any, Literal

from pishkar.tools.http import http
from pishkar.tools.registry import tool

Engine = Literal["tavily", "brave", "duckduckgo", "auto"]

_DEFAULT_FALLBACK_ORDER: tuple[str, ...] = ("tavily", "brave", "duckduckgo")


def _engines_for(preferred: str) -> list[str]:
    """Return the ordered engine list with `preferred` first."""
    if preferred == "auto":
        preferred = os.environ.get("PISHKAR_SEARCH_ENGINE", "").strip() or _auto_pick()
    rest = [e for e in _DEFAULT_FALLBACK_ORDER if e != preferred]
    return [preferred, *rest]


def _auto_pick() -> str:
    if os.environ.get("TAVILY_API_KEY"):
        return "tavily"
    if os.environ.get("BRAVE_API_KEY"):
        return "brave"
    return "duckduckgo"


@tool(
    description=(
        "Search the web. `engine` can be 'tavily', 'brave', 'duckduckgo', "
        "or 'auto' (default — uses PISHKAR_SEARCH_ENGINE or whichever API "
        "key is configured). Falls back to other engines on API errors."
    )
)
async def search(
    query: str,
    engine: Engine = "auto",
    max_results: int = 5,
) -> str:
    errors: list[str] = []
    for name in _engines_for(engine):
        runner = _ENGINES.get(name)
        if runner is None:
            errors.append(f"{name}: unknown engine")
            continue
        try:
            return await runner(query, max_results)
        except Exception as e:  # noqa: BLE001 — fall back to next engine
            errors.append(f"{name}: {type(e).__name__}: {e}")
    return "[search failed across all engines]\n" + "\n".join(errors)


async def _tavily(query: str, max_results: int) -> str:
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        raise RuntimeError("TAVILY_API_KEY not set")
    raw = await http(
        url="https://api.tavily.com/search",
        method="POST",
        headers={"Authorization": f"Bearer {api_key}"},
        json_body={
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
        },
    )
    data = _json.loads(raw)
    lines = [f"# Tavily results for: {query}"]
    answer = data.get("answer")
    if answer:
        lines.append(f"\n**Answer:** {answer}\n")
    for r in data.get("results", [])[:max_results]:
        lines.append(f"\n- **{r.get('title', '')}** — {r.get('url', '')}")
        content = r.get("content", "").strip()
        if content:
            lines.append(f"  {content}")
    return "\n".join(lines)


async def _brave(query: str, max_results: int) -> str:
    api_key = os.environ.get("BRAVE_API_KEY")
    if not api_key:
        raise RuntimeError("BRAVE_API_KEY not set")
    qs = urllib.parse.urlencode({"q": query, "count": max_results})
    raw = await http(
        url=f"https://api.search.brave.com/res/v1/web/search?{qs}",
        headers={
            "Accept": "application/json",
            "X-Subscription-Token": api_key,
        },
    )
    data = _json.loads(raw)
    web = data.get("web", {}).get("results", [])
    lines = [f"# Brave results for: {query}"]
    for r in web[:max_results]:
        lines.append(f"\n- **{r.get('title', '')}** — {r.get('url', '')}")
        desc = (r.get("description") or "").strip()
        if desc:
            lines.append(f"  {desc}")
    return "\n".join(lines)


_DDG_LINK = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
    re.DOTALL,
)
_DDG_SNIPPET = re.compile(
    r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
    re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    return _TAG_RE.sub("", s).strip()


async def _duckduckgo(query: str, max_results: int) -> str:
    qs = urllib.parse.urlencode({"q": query})
    html = await http(
        url=f"https://html.duckduckgo.com/html/?{qs}",
        headers={"User-Agent": "Mozilla/5.0 (Pishkar)"},
    )
    titles = _DDG_LINK.findall(html)
    snippets = _DDG_SNIPPET.findall(html)
    lines = [f"# DuckDuckGo results for: {query}"]
    for i, (href, title_html) in enumerate(titles[:max_results]):
        title = _strip_html(title_html)
        url = urllib.parse.unquote(href)
        # DDG wraps real URLs in a redirect; strip the `uddg=` prefix.
        m = re.search(r"uddg=([^&]+)", url)
        if m:
            url = urllib.parse.unquote(m.group(1))
        lines.append(f"\n- **{title}** — {url}")
        if i < len(snippets):
            snippet = _strip_html(snippets[i])
            if snippet:
                lines.append(f"  {snippet}")
    if len(titles) == 0:
        raise RuntimeError("DuckDuckGo returned no parseable results")
    return "\n".join(lines)


_ENGINES: dict[str, Any] = {
    "tavily": _tavily,
    "brave": _brave,
    "duckduckgo": _duckduckgo,
}


__all__ = ["search"]
