from typing import Any

from pishkar.gateway.hooks import AFTER_LLM, ON_TURN_COMPLETE, HookManager
from pishkar.observability.langfuse_sink import LangFuseSink


class FakeTrace:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.generations: list[dict[str, Any]] = []
        self.updates: list[dict[str, Any]] = []

    def generation(self, **kwargs: Any) -> None:
        self.generations.append(kwargs)

    def update(self, **kwargs: Any) -> None:
        self.updates.append(kwargs)


class FakeLangFuse:
    def __init__(self) -> None:
        self.traces: list[FakeTrace] = []

    def trace(self, **kwargs: Any) -> FakeTrace:
        t = FakeTrace(**kwargs)
        self.traces.append(t)
        return t


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

    assert len(client.traces) == 1
    assert client.traces[0].kwargs == {
        "id": "t1", "session_id": "s1", "user_id": "ali"
    }
    [gen] = client.traces[0].generations
    assert gen["model"] == "claude-opus"
    assert gen["usage"] == {"input": 10, "output": 5}
    assert gen["metadata"] == {"stop_reason": "end_turn"}


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

    assert len(client.traces) == 1
    assert len(client.traces[0].generations) == 2


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

    [trace] = client.traces
    assert trace.updates == [{"metadata": {"final_stop_reason": "end_turn"}}]


async def test_on_turn_complete_without_trace_is_noop() -> None:
    client = FakeLangFuse()
    sink = LangFuseSink(client)
    hm = HookManager()
    sink.attach(hm)

    hm.emit(ON_TURN_COMPLETE, turn_id="ghost", session_id="s", stop_reason="end_turn")
    await hm.drain()
    assert client.traces == []


async def test_client_failure_is_swallowed_by_hooks() -> None:
    class ExplodingClient:
        def trace(self, **_: Any) -> Any:
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
