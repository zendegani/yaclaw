from typing import Any

from pishkar.gateway.hooks import AFTER_LLM, ON_TURN_COMPLETE, HookManager
from pishkar.observability.phoenix_sink import PhoenixSink


class FakeSpan:
    def __init__(self, name: str, attributes: dict[str, Any] | None) -> None:
        self.name = name
        self.attributes: dict[str, Any] = dict(attributes or {})
        self.ended = False

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def end(self) -> None:
        self.ended = True


class FakeTracer:
    def __init__(self) -> None:
        self.spans: list[FakeSpan] = []

    def start_span(
        self, name: str, *, attributes: dict[str, Any] | None = None
    ) -> FakeSpan:
        span = FakeSpan(name, attributes)
        self.spans.append(span)
        return span


async def test_after_llm_opens_span_with_identity_attributes() -> None:
    tracer = FakeTracer()
    hm = HookManager()
    PhoenixSink(tracer).attach(hm)

    hm.emit(
        AFTER_LLM,
        turn_id="t1", session_id="s1", user_id="ali",
        model="claude-opus", stop_reason="end_turn",
        input_tokens=10, output_tokens=5,
    )
    await hm.drain()

    [span] = tracer.spans
    assert span.name == "pishkar.turn"
    assert span.attributes["turn.id"] == "t1"
    assert span.attributes["session.id"] == "s1"
    assert span.attributes["user.id"] == "ali"
    assert span.attributes["llm.model"] == "claude-opus"
    assert span.attributes["llm.token_count.prompt"] == 10
    assert span.attributes["llm.token_count.completion"] == 5
    assert span.attributes["llm.stop_reason"] == "end_turn"
    assert span.ended is False


async def test_after_llm_records_chat_messages_and_assistant_text() -> None:
    tracer = FakeTracer()
    hm = HookManager()
    PhoenixSink(tracer).attach(hm)

    hm.emit(
        AFTER_LLM,
        turn_id="t1", session_id="s1", user_id="ali",
        model="claude-opus", stop_reason="end_turn",
        input_tokens=10, output_tokens=5,
        messages=[{"role": "user", "content": "hi there"}],
        system="be concise",
        assistant_text="hello!",
        tool_calls=[],
    )
    await hm.drain()

    [span] = tracer.spans
    assert span.attributes["openinference.span.kind"] == "LLM"
    assert span.attributes["llm.input_messages.0.message.role"] == "system"
    assert span.attributes["llm.input_messages.0.message.content"] == "be concise"
    assert span.attributes["llm.input_messages.1.message.role"] == "user"
    assert span.attributes["llm.input_messages.1.message.content"] == "hi there"
    assert span.attributes["llm.output_messages.0.message.role"] == "assistant"
    assert span.attributes["llm.output_messages.0.message.content"] == "hello!"
    assert span.attributes["output.value"] == "hello!"
    assert "input.value" in span.attributes


async def test_after_llm_records_tool_calls_on_output_message() -> None:
    tracer = FakeTracer()
    hm = HookManager()
    PhoenixSink(tracer).attach(hm)

    hm.emit(
        AFTER_LLM,
        turn_id="t1", session_id="s1", user_id="ali",
        model="m", stop_reason="tool_use",
        input_tokens=1, output_tokens=1,
        messages=[{"role": "user", "content": "do it"}],
        assistant_text="",
        tool_calls=[{"id": "call_1", "name": "search", "arguments": '{"q":"x"}'}],
    )
    await hm.drain()

    [span] = tracer.spans
    a = span.attributes
    assert a["llm.output_messages.0.message.role"] == "assistant"
    assert a["llm.output_messages.0.message.tool_calls.0.tool_call.id"] == "call_1"
    assert (
        a["llm.output_messages.0.message.tool_calls.0.tool_call.function.name"]
        == "search"
    )
    assert (
        a["llm.output_messages.0.message.tool_calls.0.tool_call.function.arguments"]
        == '{"q":"x"}'
    )


async def test_two_after_llm_calls_share_span_and_sum_tokens() -> None:
    tracer = FakeTracer()
    hm = HookManager()
    PhoenixSink(tracer).attach(hm)

    for _ in range(2):
        hm.emit(
            AFTER_LLM,
            turn_id="t1", session_id="s1", user_id="ali",
            model="m", stop_reason="tool_use",
            input_tokens=3, output_tokens=4,
        )
    await hm.drain()

    [span] = tracer.spans
    assert span.attributes["llm.token_count.prompt"] == 6
    assert span.attributes["llm.token_count.completion"] == 8


async def test_on_turn_complete_ends_span_with_final_stop_reason() -> None:
    tracer = FakeTracer()
    hm = HookManager()
    PhoenixSink(tracer).attach(hm)

    hm.emit(
        AFTER_LLM,
        turn_id="t1", session_id="s1", user_id="ali",
        model="m", stop_reason="end_turn",
        input_tokens=1, output_tokens=1,
    )
    hm.emit(ON_TURN_COMPLETE, turn_id="t1", session_id="s1", stop_reason="end_turn")
    await hm.drain()

    [span] = tracer.spans
    assert span.ended is True
    assert span.attributes["llm.final_stop_reason"] == "end_turn"


async def test_on_turn_complete_without_span_is_noop() -> None:
    tracer = FakeTracer()
    hm = HookManager()
    PhoenixSink(tracer).attach(hm)

    hm.emit(ON_TURN_COMPLETE, turn_id="ghost", session_id="s", stop_reason="end_turn")
    await hm.drain()
    assert tracer.spans == []


async def test_tracer_failure_is_swallowed_by_hooks() -> None:
    class ExplodingTracer:
        def start_span(self, *_: Any, **__: Any) -> Any:
            raise RuntimeError("Phoenix down")

    hm = HookManager()
    PhoenixSink(ExplodingTracer()).attach(hm)
    seen: list[int] = []
    hm.on(AFTER_LLM, lambda **_: seen.append(1))

    hm.emit(
        AFTER_LLM,
        turn_id="t", session_id="s", user_id="ali", model="m",
        stop_reason="end_turn", input_tokens=1, output_tokens=1,
    )
    await hm.drain()
    assert seen == [1]
