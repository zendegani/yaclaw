from typing import Any

from pishkar.gateway.hooks import AFTER_LLM, ON_TURN_COMPLETE, HookManager
from pishkar.observability.langfuse_sink import LangFuseSink


class FakeGeneration:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.ended = False

    def end(self) -> None:
        self.ended = True


class FakeSpan:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.generations: list[FakeGeneration] = []
        self.trace_updates: list[dict[str, Any]] = []
        self.ended = False

    def update_trace(self, **kwargs: Any) -> None:
        self.trace_updates.append(kwargs)

    def start_generation(self, **kwargs: Any) -> FakeGeneration:
        g = FakeGeneration(**kwargs)
        self.generations.append(g)
        return g

    def end(self) -> None:
        self.ended = True


class FakeLangFuse:
    def __init__(self) -> None:
        self.spans: list[FakeSpan] = []

    def start_span(self, **kwargs: Any) -> FakeSpan:
        s = FakeSpan(**kwargs)
        self.spans.append(s)
        return s


async def test_after_llm_starts_trace_and_records_generation() -> None:
    client = FakeLangFuse()
    sink = LangFuseSink(client)
    hm = HookManager()
    sink.attach(hm)

    hm.emit(
        AFTER_LLM,
        turn_id="t1", session_id="s1", user_id="ali",
        model="claude-opus", stop_reason="end_turn",
        input_tokens=10, output_tokens=5,
    )
    await hm.drain()

    assert len(client.spans) == 1
    span = client.spans[0]
    assert span.kwargs == {"name": "t1"}
    assert span.trace_updates == [{"session_id": "s1", "user_id": "ali"}]
    [gen] = span.generations
    assert gen.kwargs["model"] == "claude-opus"
    assert gen.kwargs["usage_details"] == {"input": 10, "output": 5}
    assert gen.kwargs["metadata"] == {"stop_reason": "end_turn"}
    assert gen.ended is True


async def test_two_after_llm_calls_share_one_trace_per_turn() -> None:
    client = FakeLangFuse()
    sink = LangFuseSink(client)
    hm = HookManager()
    sink.attach(hm)

    for _ in range(2):
        hm.emit(
            AFTER_LLM,
            turn_id="t1", session_id="s1", user_id="ali",
            model="m", stop_reason="tool_use",
            input_tokens=1, output_tokens=1,
        )
    await hm.drain()

    assert len(client.spans) == 1
    assert len(client.spans[0].generations) == 2


async def test_on_turn_complete_finalizes_trace() -> None:
    client = FakeLangFuse()
    sink = LangFuseSink(client)
    hm = HookManager()
    sink.attach(hm)

    hm.emit(
        AFTER_LLM,
        turn_id="t1", session_id="s1", user_id="ali",
        model="m", stop_reason="end_turn",
        input_tokens=1, output_tokens=1,
    )
    hm.emit(ON_TURN_COMPLETE, turn_id="t1", session_id="s1", stop_reason="end_turn")
    await hm.drain()

    [span] = client.spans
    assert span.trace_updates == [
        {"session_id": "s1", "user_id": "ali"},
        {"metadata": {"final_stop_reason": "end_turn"}},
    ]
    assert span.ended is True


async def test_on_turn_complete_without_trace_is_noop() -> None:
    client = FakeLangFuse()
    sink = LangFuseSink(client)
    hm = HookManager()
    sink.attach(hm)

    hm.emit(ON_TURN_COMPLETE, turn_id="ghost", session_id="s", stop_reason="end_turn")
    await hm.drain()
    assert client.spans == []


async def test_client_failure_is_swallowed_by_hooks() -> None:
    class ExplodingClient:
        def start_span(self, **_: Any) -> Any:
            raise RuntimeError("LangFuse down")

    sink = LangFuseSink(ExplodingClient())
    hm = HookManager()
    sink.attach(hm)
    # Adding a second handler proves the first's failure didn't poison the chain.
    seen: list[int] = []
    hm.on(AFTER_LLM, lambda **_: seen.append(1))

    hm.emit(
        AFTER_LLM,
        turn_id="t", session_id="s", user_id="ali", model="m",
        stop_reason="end_turn", input_tokens=1, output_tokens=1,
    )
    await hm.drain()
    assert seen == [1]
