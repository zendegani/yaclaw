"""Tests for the MiniMax adapter and dispatcher wiring."""

import json
from typing import Any

import httpx
import pytest

from pishkar.providers.litellm_provider import LiteLLMProvider
from pishkar.providers.minimax import (
    MINIMAX_BASE_URL,
    is_minimax_model,
    minimax_acompletion,
)
from pishkar.runtime import (
    EXTRA_ENV_REQUIRED,
    _build_completion_dispatcher,
    discover_available_models,
)


def _sse(chunks: list[dict[str, Any]]) -> bytes:
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return body.encode()


def _mock_transport(captured: dict[str, Any], sse_body: bytes) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, content=sse_body)
    return httpx.MockTransport(handler)


async def test_request_shape() -> None:
    """URL carries GroupId; bearer auth set; model prefix stripped."""
    captured: dict[str, Any] = {}
    body = _sse([{"choices": [{"delta": {"content": "ok"}, "index": 0}]}])

    chunks = [
        c
        async for c in await minimax_acompletion(
            model="minimax/MiniMax-M2",
            messages=[{"role": "user", "content": "ping"}],
            stream=True,
            max_tokens=16,
            api_key="sk-cp-test",
            group_id="GID-123",
            transport=_mock_transport(captured, body),
        )
    ]

    assert captured["url"] == f"{MINIMAX_BASE_URL}?GroupId=GID-123"
    assert captured["headers"]["authorization"] == "Bearer sk-cp-test"
    assert captured["json"]["model"] == "MiniMax-M2"
    assert captured["json"]["stream"] is True
    assert captured["json"]["max_tokens"] == 16
    assert chunks[0]["choices"][0]["delta"]["content"] == "ok"


async def test_litellm_provider_consumes_minimax_chunks() -> None:
    """OpenAI-shaped SSE chunks flow through `_parse_chunk` unchanged."""
    captured: dict[str, Any] = {}
    body = _sse([
        {"choices": [{"delta": {"content": "hello"}, "index": 0}]},
        {"choices": [{"delta": {"content": " world"}, "index": 0,
                      "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "stop", "index": 0}],
         "usage": {"prompt_tokens": 7, "completion_tokens": 2}},
    ])
    transport = _mock_transport(captured, body)

    async def completion(**kwargs: Any) -> Any:
        return await minimax_acompletion(
            api_key="sk-cp-test", group_id="GID", transport=transport, **kwargs
        )

    provider = LiteLLMProvider(completion)
    chunks = [
        c
        async for c in provider.stream(
            model="minimax/MiniMax-M2",
            messages=[{"role": "user", "content": "hi"}],
            system="be terse",
        )
    ]

    texts = [c.text for c in chunks if c.text]
    assert "".join(texts) == "hello world"
    stops = [c.stop_reason for c in chunks if c.stop_reason]
    assert stops == ["stop"]
    final = chunks[-1]
    assert final.usage is not None
    assert final.usage.input_tokens == 7
    assert final.usage.output_tokens == 2
    assert captured["json"]["messages"][0] == {"role": "system", "content": "be terse"}


async def test_dispatcher_routes_minimax_only() -> None:
    """Non-MiniMax models hit the Router; MiniMax models bypass it."""
    router_calls: list[str] = []
    minimax_call: dict[str, Any] = {}

    async def router_completion(*, model: str, **kwargs: Any) -> str:
        router_calls.append(model)
        return f"router:{model}"

    body = _sse([{"choices": [{"delta": {"content": "x"}, "index": 0}]}])
    transport = _mock_transport(minimax_call, body)

    # Wrap so the dispatcher can pass api_key/group_id/transport through.
    async def minimax_via_mock(**kwargs: Any) -> Any:
        return await minimax_acompletion(
            api_key="k", group_id="g", transport=transport, **kwargs
        )

    import pishkar.runtime as runtime

    dispatch = _build_completion_dispatcher(router_completion)
    monkey_target = runtime.minimax_acompletion  # noqa: F841 — sanity
    runtime.minimax_acompletion = minimax_via_mock  # type: ignore[assignment]
    try:
        stream = await dispatch(model="minimax/MiniMax-M2",
                                 messages=[{"role": "user", "content": "hi"}])
        async for _ in stream:
            pass
        result = await dispatch(model="anthropic/claude-opus-4-7",
                                 messages=[{"role": "user", "content": "hi"}])
    finally:
        runtime.minimax_acompletion = monkey_target  # type: ignore[assignment]

    assert router_calls == ["anthropic/claude-opus-4-7"]
    assert result == "router:anthropic/claude-opus-4-7"
    assert minimax_call["json"]["model"] == "MiniMax-M2"


async def test_dispatcher_without_router_rejects_non_minimax() -> None:
    dispatch = _build_completion_dispatcher(None)
    with pytest.raises(RuntimeError):
        await dispatch(model="gpt-4o-mini",
                       messages=[{"role": "user", "content": "hi"}])


def test_is_minimax_model() -> None:
    assert is_minimax_model("minimax/MiniMax-M2")
    assert not is_minimax_model("anthropic/claude-opus-4-7")
    assert not is_minimax_model("MiniMax-M2")


def test_discover_requires_group_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Half-configured MiniMax (key without GroupId) is hidden so it doesn't
    show up in the model catalog and then 500 at call time."""
    for k, _, _ in [("ANTHROPIC_API_KEY", "", ""), ("OPENAI_API_KEY", "", ""),
                    ("GEMINI_API_KEY", "", ""), ("GOOGLE_API_KEY", "", ""),
                    ("OPENROUTER_API_KEY", "", ""), ("GROQ_API_KEY", "", ""),
                    ("MOONSHOT_API_KEY", "", ""), ("DASHSCOPE_API_KEY", "", ""),
                    ("MINIMAX_API_KEY", "", ""), ("MINIMAX_GROUP_ID", "", "")]:
        monkeypatch.delenv(k, raising=False)

    monkeypatch.setenv("MINIMAX_API_KEY", "sk-cp-x")
    assert "minimax" not in discover_available_models()

    monkeypatch.setenv("MINIMAX_GROUP_ID", "g-1")
    assert "minimax" in discover_available_models()


def test_extra_env_required_table_lists_minimax() -> None:
    """Guard against forgetting MINIMAX_GROUP_ID if PROVIDER_KEYS evolves."""
    assert "MINIMAX_GROUP_ID" in EXTRA_ENV_REQUIRED["minimax"]
