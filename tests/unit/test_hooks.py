import asyncio
from collections.abc import AsyncIterator
from typing import Any

from pishkar.core.agent import run_turn
from pishkar.core.messages import InboundMessage
from pishkar.gateway.hooks import (
    AFTER_LLM,
    BEFORE_TOOL,
    ON_TOOL_RESULT,
    ON_TURN_COMPLETE,
    HookManager,
)
from pishkar.providers.base import ModelProvider, ProviderChunk, ToolCallDelta, Usage
from pishkar.tools.registry import ToolRegistry, tool
from pishkar.tools.runner import SubprocessToolRunner


async def test_register_and_emit_sync_handler() -> None:
    hm = HookManager()
    seen: list[dict[str, Any]] = []
    hm.on("custom", lambda **p: seen.append(p))
    hm.emit("custom", x=1, y="z")
    await hm.drain()
    assert seen == [{"x": 1, "y": "z"}]


async def test_register_and_emit_async_handler() -> None:
    hm = HookManager()
    seen: list[int] = []

    async def collect(n: int) -> None:
        await asyncio.sleep(0)
        seen.append(n)

    hm.on("e", collect)
    for i in range(3):
        hm.emit("e", n=i)
    await hm.drain()
    assert sorted(seen) == [0, 1, 2]


async def test_handler_exception_is_swallowed() -> None:
    hm = HookManager()
    seen: list[str] = []

    def boom(**_: Any) -> None:
        raise RuntimeError("kaboom")

    hm.on("e", boom)
    hm.on("e", lambda **_: seen.append("ok"))
    hm.emit("e")  # must not raise
    await hm.drain()
    assert seen == ["ok"]


async def test_emit_with_no_handlers_is_noop() -> None:
    HookManager().emit("nothing", a=1)  # no exception


async def test_drain_with_nothing_pending() -> None:
    await HookManager().drain()


async def test_emit_outside_event_loop_drops_silently() -> None:
    # Run synchronously: no running loop on the calling thread.
    hm = HookManager()
    hm.on("e", lambda **_: None)
    # Must not raise even though there's no loop running here.
    asyncio.new_event_loop().close()
    # We're inside a pytest-asyncio test so a loop *is* running; just verify
    # the no-loop branch by simulating via a thread.

    import threading

    err: list[BaseException] = []

    def in_thread() -> None:
        try:
            hm.emit("e")
        except BaseException as e:  # noqa: BLE001
            err.append(e)

    t = threading.Thread(target=in_thread)
    t.start()
    t.join()
    assert err == []


# ---- Integration: runner emits before_tool / on_tool_result ----------------


@tool()
async def echo(text: str) -> str:
    return f"echo:{text}"


async def test_runner_emits_hooks() -> None:
    hm = HookManager()
    seen: list[tuple[str, dict[str, Any]]] = []
    hm.on(BEFORE_TOOL, lambda **p: seen.append(("before", p)))
    hm.on(ON_TOOL_RESULT, lambda **p: seen.append(("result", p)))

    reg = ToolRegistry()
    reg.register(echo)
    runner = SubprocessToolRunner(reg, hooks=hm)
    out = await runner.run("echo", {"text": "hi"})
    await hm.drain()

    assert out.content == "echo:hi"
    kinds = [k for k, _ in seen]
    assert kinds == ["before", "result"]
    assert seen[0][1]["tool_name"] == "echo"
    assert seen[1][1]["result"].content == "echo:hi"


async def test_runner_emits_on_tool_result_even_for_errors() -> None:
    hm = HookManager()
    results: list[Any] = []
    hm.on(ON_TOOL_RESULT, lambda **p: results.append(p["result"]))

    @tool()
    async def boom() -> str:
        raise ValueError("nope")

    reg = ToolRegistry()
    reg.register(boom)
    runner = SubprocessToolRunner(reg, hooks=hm)
    await runner.run("boom", {})
    await hm.drain()

    assert len(results) == 1 and results[0].is_error


# ---- Integration: agent loop emits after_llm / on_turn_complete ------------


class _Provider(ModelProvider):
    def __init__(self, scripts: list[list[ProviderChunk]]) -> None:
        self._scripts = scripts
        self._i = 0

    async def stream(self, **_: Any) -> AsyncIterator[ProviderChunk]:
        for c in self._scripts[self._i]:
            yield c
        self._i += 1


async def test_agent_emits_after_llm_and_on_turn_complete() -> None:
    hm = HookManager()
    after: list[dict[str, Any]] = []
    completes: list[dict[str, Any]] = []
    hm.on(AFTER_LLM, lambda **p: after.append(p))
    hm.on(ON_TURN_COMPLETE, lambda **p: completes.append(p))

    provider = _Provider([[
        ProviderChunk(text="ok"),
        ProviderChunk(stop_reason="stop"),
        ProviderChunk(usage=Usage(input_tokens=4, output_tokens=2)),
    ]])
    runner = SubprocessToolRunner(ToolRegistry())

    [_ async for _ in run_turn(
        user_message=InboundMessage(user_id="ali", session_id="s1",
                                    channel="cli", content="hi"),
        history=[],
        provider=provider,
        runner=runner,
        hooks=hm,
    )]
    await hm.drain()

    assert len(after) == 1
    assert after[0]["stop_reason"] == "end_turn"
    assert after[0]["input_tokens"] == 4
    assert len(completes) == 1
    assert completes[0]["stop_reason"] == "end_turn"


async def test_agent_emits_on_turn_complete_for_max_turns() -> None:
    hm = HookManager()
    completes: list[dict[str, Any]] = []
    hm.on(ON_TURN_COMPLETE, lambda **p: completes.append(p))

    tc = ToolCallDelta(index=0, id="c1", name="echo", arguments='{"text":"x"}')
    provider = _Provider([
        [ProviderChunk(tool_calls=[tc]), ProviderChunk(stop_reason="tool_calls")],
    ] * 5)
    reg = ToolRegistry()
    reg.register(echo)
    runner = SubprocessToolRunner(reg)

    [_ async for _ in run_turn(
        user_message=InboundMessage(user_id="ali", session_id="s1",
                                    channel="cli", content="loop"),
        history=[],
        provider=provider,
        runner=runner,
        hooks=hm,
        max_turns=2,
    )]
    await hm.drain()
    assert completes[-1]["stop_reason"] == "max_turns"
