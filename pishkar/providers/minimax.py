"""MiniMax chatcompletion_v2 client — OpenAI-shaped, but at a non-standard
path and with `GroupId` as a query parameter.

LiteLLM has no native MiniMax provider, and its `openai/` shim only posts
to `<api_base>/chat/completions`, which doesn't reach MiniMax's
`/v1/text/chatcompletion_v2`. So this module ships the smallest possible
shim: an async function shaped like `litellm.acompletion(stream=True, ...)`
that yields dicts compatible with the OpenAI streaming protocol. The
existing `_parse_chunk` in `litellm_provider.py` consumes them unchanged,
so the agent loop, BudgetedProvider, and observability all keep working.
"""

import json
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx

MINIMAX_BASE_URL = "https://api.minimax.io/v1/text/chatcompletion_v2"
MINIMAX_MODEL_PREFIX = "minimax/"


def is_minimax_model(model: str) -> bool:
    return model.startswith(MINIMAX_MODEL_PREFIX)


def _strip_prefix(model: str) -> str:
    return model[len(MINIMAX_MODEL_PREFIX):] if is_minimax_model(model) else model


async def minimax_acompletion(
    *,
    model: str,
    messages: list[dict[str, Any]],
    stream: bool = True,
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int | None = None,
    api_key: str | None = None,
    group_id: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    **_: Any,
) -> AsyncIterator[dict[str, Any]]:
    """Stream a MiniMax completion as OpenAI-shaped chunk dicts.

    Only the streaming path is implemented — the agent loop always sets
    `stream=True`. Non-streaming would mean a second code path with no
    caller today; add it when something needs it."""
    if not stream:
        raise NotImplementedError("MiniMax adapter only supports stream=True")

    api_key = api_key or os.environ.get("MINIMAX_API_KEY")
    group_id = group_id or os.environ.get("MINIMAX_GROUP_ID")
    if not api_key or not group_id:
        raise RuntimeError(
            "MINIMAX_API_KEY and MINIMAX_GROUP_ID must be set to call MiniMax."
        )

    payload: dict[str, Any] = {
        "model": _strip_prefix(model),
        "messages": messages,
        "stream": True,
    }
    if tools:
        payload["tools"] = tools
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    url = f"{MINIMAX_BASE_URL}?GroupId={group_id}"

    return _iter_sse(url, headers, payload, transport)


async def _iter_sse(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    transport: httpx.AsyncBaseTransport | None,
) -> AsyncIterator[dict[str, Any]]:
    timeout = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)
    async with (
        httpx.AsyncClient(timeout=timeout, transport=transport) as client,
        client.stream("POST", url, headers=headers, json=payload) as resp,
    ):
        if resp.status_code != 200:
            body = (await resp.aread()).decode("utf-8", errors="replace")
            raise httpx.HTTPStatusError(
                f"MiniMax {resp.status_code}: {body[:500]}",
                request=resp.request,
                response=resp,
            )
        async for line in resp.aiter_lines():
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                return
            try:
                yield json.loads(data)
            except json.JSONDecodeError:
                continue


__all__ = [
    "MINIMAX_MODEL_PREFIX",
    "is_minimax_model",
    "minimax_acompletion",
]
