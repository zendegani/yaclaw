import asyncio
from typing import Any

import pytest

from pishkar.tools.registry import ToolRegistry, tool
from pishkar.tools.runner import (
    DEFAULT_MAX_RESULT_BYTES,
    DEFAULT_TIMEOUT_S,
    SubprocessToolRunner,
    ToolRunner,
)


@tool()
async def echo(text: str) -> str:
    return text


@tool()
async def slow(seconds: float) -> str:
    await asyncio.sleep(seconds)
    return "done"


@tool()
async def boom() -> str:
    raise ValueError("kaboom")


@tool()
async def big(size: int) -> str:
    return "x" * size


@tool()
async def number() -> int:
    return 42


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register_many(echo, slow, boom, big, number)
    return reg


def test_runner_satisfies_protocol() -> None:
    assert isinstance(SubprocessToolRunner(_registry()), ToolRunner)


def test_runner_defaults() -> None:
    r = SubprocessToolRunner(_registry())
    assert r._default_timeout_s == DEFAULT_TIMEOUT_S
    assert r._default_max_bytes == DEFAULT_MAX_RESULT_BYTES


async def test_run_returns_tool_output() -> None:
    runner = SubprocessToolRunner(_registry())
    result = await runner.run("echo", {"text": "hi"})
    assert result.content == "hi"
    assert not result.is_error and not result.truncated


async def test_run_coerces_non_string_results() -> None:
    runner = SubprocessToolRunner(_registry())
    result = await runner.run("number", {})
    assert result.content == "42"


async def test_run_timeout() -> None:
    runner = SubprocessToolRunner(_registry(), default_timeout_s=0.05)
    result = await runner.run("slow", {"seconds": 1.0})
    assert result.timed_out and result.is_error
    assert "timeout" in result.content.lower()


async def test_run_per_call_timeout_overrides_default() -> None:
    runner = SubprocessToolRunner(_registry(), default_timeout_s=10.0)
    result = await runner.run("slow", {"seconds": 1.0}, timeout_s=0.05)
    assert result.timed_out


async def test_run_catches_tool_exception() -> None:
    runner = SubprocessToolRunner(_registry())
    result = await runner.run("boom", {})
    assert result.is_error and not result.timed_out
    assert "ValueError" in result.content
    assert "kaboom" in result.content


async def test_run_truncates_oversize_result() -> None:
    runner = SubprocessToolRunner(_registry(), default_max_bytes=200)
    result = await runner.run("big", {"size": 10_000})
    assert result.truncated and not result.is_error
    assert len(result.content.encode("utf-8")) <= 200
    assert "truncated" in result.content


async def test_run_does_not_truncate_when_under_cap() -> None:
    runner = SubprocessToolRunner(_registry(), default_max_bytes=10_000)
    result = await runner.run("big", {"size": 100})
    assert not result.truncated
    assert result.content == "x" * 100


async def test_approval_denial() -> None:
    async def deny(name: str, args: dict[str, Any]) -> bool:
        return False

    runner = SubprocessToolRunner(_registry(), approval_fn=deny)
    result = await runner.run("echo", {"text": "hi"})
    assert result.denied and result.is_error
    assert "denied" in result.content


async def test_approval_allow_runs_tool() -> None:
    seen: list[tuple[str, dict[str, Any]]] = []

    async def allow(name: str, args: dict[str, Any]) -> bool:
        seen.append((name, args))
        return True

    runner = SubprocessToolRunner(_registry(), approval_fn=allow)
    result = await runner.run("echo", {"text": "hi"})
    assert result.content == "hi" and not result.denied
    assert seen == [("echo", {"text": "hi"})]


async def test_unknown_tool_returns_error() -> None:
    runner = SubprocessToolRunner(_registry())
    result = await runner.run("ghost", {})
    assert result.is_error
    assert "ghost" in result.content


async def test_invalid_args_returns_error() -> None:
    runner = SubprocessToolRunner(_registry())
    result = await runner.run("echo", {"text": 123, "extra": "junk"})
    # pydantic will coerce 123 to "123" by default; just ensure no crash
    assert not result.is_error or "ValidationError" in result.content


async def test_pydantic_validation_error_caught() -> None:
    runner = SubprocessToolRunner(_registry())
    # 'big' requires int 'size' — passing a non-coercible value
    result = await runner.run("big", {"size": "not-a-number"})
    assert result.is_error
    assert "ValidationError" in result.content or "validation" in result.content.lower()


@pytest.mark.parametrize("max_bytes", [50, 100, 500])
async def test_truncation_respects_byte_cap(max_bytes: int) -> None:
    runner = SubprocessToolRunner(_registry(), default_max_bytes=max_bytes)
    result = await runner.run("big", {"size": 10_000})
    assert len(result.content.encode("utf-8")) <= max_bytes
