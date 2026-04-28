from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest

from pishkar.providers.base import ModelProvider, ProviderChunk
from pishkar.providers.litellm_provider import LiteLLMProvider, _parse_chunk


def _delta_chunk(content: str | None = None, tool_calls: list[Any] | None = None,
                 finish: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(
            delta=SimpleNamespace(content=content, tool_calls=tool_calls or []),
            finish_reason=finish,
        )],
        usage=None,
    )


def _usage_chunk(prompt: int, completion: int) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion),
    )


def test_litellm_provider_satisfies_protocol() -> None:
    p = LiteLLMProvider(completion=lambda **_: None)  # type: ignore[arg-type,return-value]
    assert isinstance(p, ModelProvider)


def test_parse_chunk_text() -> None:
    out = _parse_chunk(_delta_chunk(content="hello"))
    assert out.text == "hello"
    assert out.tool_calls == []
    assert out.stop_reason is None


def test_parse_chunk_tool_call_delta() -> None:
    tc = SimpleNamespace(
        index=0,
        id="call_1",
        function=SimpleNamespace(name="bash", arguments='{"cmd":'),
    )
    out = _parse_chunk(_delta_chunk(tool_calls=[tc]))
    assert len(out.tool_calls) == 1
    assert out.tool_calls[0].id == "call_1"
    assert out.tool_calls[0].name == "bash"
    assert out.tool_calls[0].arguments == '{"cmd":'


def test_parse_chunk_handles_dict_shape() -> None:
    """Some LiteLLM versions emit dicts instead of pydantic models."""
    chunk = {
        "choices": [{"delta": {"content": "hi", "tool_calls": []}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 4, "completion_tokens": 2},
    }
    out = _parse_chunk(chunk)
    assert out.text == "hi"
    assert out.stop_reason == "stop"
    assert out.usage is not None
    assert out.usage.input_tokens == 4 and out.usage.output_tokens == 2


def test_parse_chunk_usage_only() -> None:
    out = _parse_chunk(_usage_chunk(10, 7))
    assert out.text is None
    assert out.usage is not None
    assert (out.usage.input_tokens, out.usage.output_tokens) == (10, 7)


async def test_stream_passes_kwargs_and_yields_parsed_chunks() -> None:
    captured: dict[str, Any] = {}

    async def fake_iter() -> AsyncIterator[Any]:
        yield _delta_chunk(content="hel")
        yield _delta_chunk(content="lo")
        yield _delta_chunk(finish="stop")
        yield _usage_chunk(3, 2)

    async def fake_completion(**kwargs: Any) -> AsyncIterator[Any]:
        captured.update(kwargs)
        return fake_iter()

    provider = LiteLLMProvider(completion=fake_completion)
    chunks: list[ProviderChunk] = []
    async for c in provider.stream(
        model="claude-opus",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "bash"}}],
        system="be brief",
        max_tokens=128,
        user_id="ali",
    ):
        chunks.append(c)

    assert captured["model"] == "claude-opus"
    assert captured["stream"] is True
    assert captured["max_tokens"] == 128
    assert captured["user"] == "ali"
    assert captured["messages"][0] == {"role": "system", "content": "be brief"}
    assert captured["messages"][1] == {"role": "user", "content": "hi"}
    assert "tools" in captured

    assert [c.text for c in chunks if c.text] == ["hel", "lo"]
    assert chunks[2].stop_reason == "stop"
    assert chunks[3].usage is not None
    assert chunks[3].usage.input_tokens == 3


async def test_stream_omits_optional_kwargs_when_none() -> None:
    captured: dict[str, Any] = {}

    async def fake_iter() -> AsyncIterator[Any]:
        if False:
            yield  # pragma: no cover

    async def fake_completion(**kwargs: Any) -> AsyncIterator[Any]:
        captured.update(kwargs)
        return fake_iter()

    provider = LiteLLMProvider(completion=fake_completion)
    async for _ in provider.stream(model="m", messages=[{"role": "user", "content": "x"}]):
        pass

    assert "tools" not in captured
    assert "max_tokens" not in captured
    assert "user" not in captured
    assert captured["messages"][0]["role"] == "user"


def test_build_router_completion_requires_litellm() -> None:
    from pishkar.providers import litellm_provider

    pytest.importorskip(
        "litellm",
        reason="litellm is an optional runtime dep; build_router_completion imports lazily",
    )
    fn = litellm_provider.build_router_completion(
        model_list=[{"model_name": "m", "litellm_params": {"model": "openai/gpt-4o-mini"}}]
    )
    assert callable(fn)
