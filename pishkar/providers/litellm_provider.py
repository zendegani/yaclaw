"""LiteLLM-backed `ModelProvider`.

The provider takes any callable shaped like `litellm.acompletion` /
`litellm.Router.acompletion`. The real router is constructed by
`build_router_completion()` (lazy import — `litellm` is only required at
runtime, not for tests). Tests inject a fake completion callable.

Chunk parsing follows the OpenAI streaming shape that LiteLLM normalizes
to: `chunk.choices[0].delta.{content, tool_calls}` plus
`chunk.choices[0].finish_reason` and a final `chunk.usage`.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, cast

from pishkar.providers.base import ModelProvider, ProviderChunk, ToolCallDelta, Usage

CompletionFunc = Callable[..., Awaitable[Any]]


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    """LiteLLM may yield pydantic models or plain dicts depending on version."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _parse_chunk(chunk: Any) -> ProviderChunk:
    choices = _attr(chunk, "choices") or []
    choice = choices[0] if choices else None
    delta = _attr(choice, "delta")
    text = _attr(delta, "content")
    finish = _attr(choice, "finish_reason")

    tool_calls: list[ToolCallDelta] = []
    for tc in _attr(delta, "tool_calls") or []:
        fn = _attr(tc, "function")
        # OpenAI-style streams arrive as accumulating JSON strings; some
        # providers (notably Gemini via LiteLLM) hand back an already-parsed
        # dict. Normalize to JSON text so the agent's accumulator works.
        raw_args = _attr(fn, "arguments")
        if isinstance(raw_args, dict | list):
            import json as _json

            args = _json.dumps(raw_args)
        else:
            args = raw_args
        tool_calls.append(
            ToolCallDelta(
                index=_attr(tc, "index", 0) or 0,
                id=_attr(tc, "id"),
                name=_attr(fn, "name"),
                arguments=args,
            )
        )

    usage_obj = _attr(chunk, "usage")
    usage = None
    if usage_obj is not None:
        usage = Usage(
            input_tokens=_attr(usage_obj, "prompt_tokens", 0) or 0,
            output_tokens=_attr(usage_obj, "completion_tokens", 0) or 0,
        )

    return ProviderChunk(
        text=text,
        tool_calls=tool_calls,
        stop_reason=finish,
        usage=usage,
    )


class LiteLLMProvider(ModelProvider):
    def __init__(self, completion: CompletionFunc) -> None:
        self._completion = completion

    async def stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
        max_tokens: int | None = None,
        user_id: str | None = None,
    ) -> AsyncIterator[ProviderChunk]:
        msgs = list(messages)
        if system:
            msgs = [{"role": "system", "content": system}, *msgs]

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": msgs,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = tools
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if user_id is not None:
            kwargs["user"] = user_id

        stream = await self._completion(**kwargs)
        async for chunk in stream:
            yield _parse_chunk(chunk)


def build_router_completion(
    model_list: list[dict[str, Any]],
    *,
    fallbacks: list[dict[str, list[str]]] | None = None,
    routing_strategy: str = "simple-shuffle",
) -> CompletionFunc:
    """Construct a `litellm.Router` and return its bound `acompletion`.

    Imported lazily so `litellm` is only required when the real provider
    is wired up (not for unit tests)."""
    from litellm import Router  # type: ignore[import-not-found]

    router = Router(
        model_list=model_list,
        fallbacks=fallbacks or [],
        routing_strategy=cast(Any, routing_strategy),
    )
    return router.acompletion  # type: ignore[no-any-return]


__all__ = ["LiteLLMProvider", "build_router_completion"]
