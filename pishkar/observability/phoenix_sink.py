"""Arize Phoenix sink — sends turn-level spans to a self-hosted Phoenix.

Phoenix is OTel-native: a span per turn, opened on the first `after_llm`
and ended on `on_turn_complete`, with token counts, stop reason, and the
chat messages set as span attributes. Attributes follow the OpenInference
semantic conventions (`openinference.span.kind`, `input.value`,
`llm.input_messages.{i}.message.{role,content}`, `llm.output_messages.…`)
so the Phoenix UI renders the conversation in its chat panel.

The `tracer` argument is duck-typed to OpenTelemetry's `Tracer`
(`start_span(name, attributes=...) -> Span` where the span exposes
`set_attribute(key, value)` and `end()`), so tests inject a fake.

All writes go through `HookManager.emit`, so failures and slowness inside
the Phoenix exporter never block the agent loop.
"""

import json
from typing import Any, Protocol

from pishkar.gateway.hooks import AFTER_LLM, ON_TURN_COMPLETE, HookManager


class _Span(Protocol):
    def set_attribute(self, key: str, value: Any) -> None: ...
    def end(self) -> None: ...


class _Tracer(Protocol):
    def start_span(
        self, name: str, *, attributes: dict[str, Any] | None = ...
    ) -> _Span: ...


class PhoenixSink:
    """Open one span per turn; accumulate token counts; close on turn end."""

    def __init__(self, tracer: _Tracer) -> None:
        self._tracer = tracer
        self._spans: dict[str, _Span] = {}
        self._totals: dict[str, dict[str, int]] = {}

    def attach(self, hooks: HookManager) -> None:
        hooks.on(AFTER_LLM, self._on_after_llm)
        hooks.on(ON_TURN_COMPLETE, self._on_turn_complete)

    async def _on_after_llm(
        self,
        *,
        turn_id: str,
        session_id: str | None = None,
        user_id: str | None = None,
        model: str = "unknown",
        stop_reason: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        messages: list[dict[str, Any]] | None = None,
        system: str | None = None,
        assistant_text: str = "",
        tool_calls: list[dict[str, Any]] | None = None,
        **_: Any,
    ) -> None:
        span = self._spans.get(turn_id)
        if span is None:
            span = self._tracer.start_span(
                "pishkar.turn",
                attributes={
                    "openinference.span.kind": "LLM",
                    "turn.id": turn_id,
                    "session.id": session_id or "",
                    "user.id": user_id or "",
                    "llm.model_name": model,
                    "llm.model": model,
                },
            )
            self._spans[turn_id] = span
            self._totals[turn_id] = {"input": 0, "output": 0}
        totals = self._totals[turn_id]
        totals["input"] += input_tokens
        totals["output"] += output_tokens
        span.set_attribute("llm.token_count.prompt", totals["input"])
        span.set_attribute("llm.token_count.completion", totals["output"])
        span.set_attribute(
            "llm.token_count.total", totals["input"] + totals["output"]
        )
        if stop_reason is not None:
            span.set_attribute("llm.stop_reason", stop_reason)

        full_input = list(messages or [])
        if system:
            full_input = [{"role": "system", "content": system}, *full_input]
        if full_input:
            span.set_attribute("input.value", json.dumps(full_input))
            span.set_attribute("input.mime_type", "application/json")
            for i, msg in enumerate(full_input):
                prefix = f"llm.input_messages.{i}.message"
                span.set_attribute(f"{prefix}.role", str(msg.get("role", "")))
                content = msg.get("content")
                if isinstance(content, str):
                    span.set_attribute(f"{prefix}.content", content)
                elif content is not None:
                    span.set_attribute(f"{prefix}.content", json.dumps(content))

        if assistant_text or tool_calls:
            prefix = "llm.output_messages.0.message"
            span.set_attribute(f"{prefix}.role", "assistant")
            if assistant_text:
                span.set_attribute(f"{prefix}.content", assistant_text)
                span.set_attribute("output.value", assistant_text)
                span.set_attribute("output.mime_type", "text/plain")
            for i, tc in enumerate(tool_calls or []):
                tc_prefix = f"{prefix}.tool_calls.{i}.tool_call"
                span.set_attribute(f"{tc_prefix}.id", str(tc.get("id", "")))
                span.set_attribute(
                    f"{tc_prefix}.function.name", str(tc.get("name", ""))
                )
                span.set_attribute(
                    f"{tc_prefix}.function.arguments",
                    str(tc.get("arguments", "")),
                )

    async def _on_turn_complete(
        self,
        *,
        turn_id: str,
        stop_reason: str | None = None,
        **_: Any,
    ) -> None:
        span = self._spans.pop(turn_id, None)
        self._totals.pop(turn_id, None)
        if span is None:
            return
        if stop_reason is not None:
            span.set_attribute("llm.final_stop_reason", stop_reason)
        span.end()


def build_phoenix_tracer(
    endpoint: str = "http://localhost:6006/v1/traces",
    project_name: str = "pishkar",
) -> Any:
    """Lazy-import factory for a Phoenix-configured OTel tracer."""
    from phoenix.otel import register  # type: ignore[import-not-found]

    tracer_provider = register(project_name=project_name, endpoint=endpoint)
    return tracer_provider.get_tracer("pishkar")


__all__ = ["PhoenixSink", "build_phoenix_tracer"]
