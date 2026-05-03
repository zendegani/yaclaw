"""`read_url` tool — fetch a webpage as clean markdown via Jina Reader.

Jina Reader (`https://r.jina.ai/<url>`) renders the page server-side and
strips menus/scripts/footers, so the LLM gets ~10× less noise than raw
HTML. Works keyless with strict rate limits; set `JINA_API_KEY` in the
environment for higher quotas.
"""

import os
import urllib.parse

from pishkar.tools.http import http
from pishkar.tools.registry import tool

_JINA_PREFIX = "https://r.jina.ai/"


@tool(description="Fetch a webpage as clean markdown (via Jina Reader).")
async def read_url(url: str) -> str:
    target = _JINA_PREFIX + urllib.parse.quote(url, safe=":/?&=#%")
    headers: dict[str, str] = {"Accept": "text/markdown"}
    api_key = os.environ.get("JINA_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return await http(url=target, headers=headers)


__all__ = ["read_url"]
