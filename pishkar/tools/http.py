"""`http` tool — minimal HTTP client built on `urllib` (no extra deps).

Wrapped in `asyncio.to_thread` so the agent loop doesn't block. Real
timeout / size enforcement comes from `ToolRunner`; this function only
sets a generous urllib-level safety timeout.
"""

import json as _json
import urllib.request
from typing import Any

from pishkar.tools.registry import tool

_URLLIB_TIMEOUT = 60.0


def _request_sync(method: str, url: str, headers: dict[str, str] | None,
                  body: str | None) -> str:
    data = body.encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=_URLLIB_TIMEOUT) as resp:  # noqa: S310
        raw: bytes = resp.read()
    return raw.decode("utf-8", errors="replace")


@tool(description="Make an HTTP request. `body` may be a string; pass JSON via `json_body`.")
async def http(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: str | None = None,
    json_body: dict[str, Any] | None = None,
) -> str:
    import asyncio

    if json_body is not None:
        body = _json.dumps(json_body)
        headers = {**(headers or {}), "Content-Type": "application/json"}
    return await asyncio.to_thread(_request_sync, method.upper(), url, headers, body)


__all__ = ["http"]
