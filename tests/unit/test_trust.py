from typing import Any

from pishkar.tools.registry import ToolRegistry, tool
from pishkar.tools.runner import SubprocessToolRunner
from pishkar.tools.trust import DEFAULT_TOOL_TRUST, TrustPolicy


@tool()
async def echo(text: str) -> str:
    return text


@tool()
async def search(query: str) -> str:
    return f"results for {query}"


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register_many(echo, search)
    return reg


# ---- TrustPolicy -----------------------------------------------------------


def test_unlisted_tool_requires_full_trust() -> None:
    policy = TrustPolicy()
    assert policy.required("bash") == "full"
    assert policy.required("some_mcp_tool") == "full"


def test_default_map_marks_read_only_tools_limited() -> None:
    policy = TrustPolicy()
    for name, level in DEFAULT_TOOL_TRUST.items():
        assert policy.required(name) == level


def test_allows_at_or_above_required_rank() -> None:
    policy = TrustPolicy()
    assert policy.allows("search", "full")
    assert policy.allows("search", "limited")
    assert not policy.allows("search", "untrusted")
    assert policy.allows("bash", "full")
    assert not policy.allows("bash", "limited")
    assert not policy.allows("bash", "untrusted")


def test_custom_policy_overrides_defaults() -> None:
    policy = TrustPolicy({"echo": "untrusted"})
    assert policy.allows("echo", "untrusted")
    # The custom map replaces the default one entirely.
    assert policy.required("search") == "full"


def _openai_schemas() -> list[dict[str, Any]]:
    return [
        {"type": "function", "function": {"name": "bash", "parameters": {}}},
        {"type": "function", "function": {"name": "search", "parameters": {}}},
        {"type": "function", "function": {"name": "read_file", "parameters": {}}},
    ]


def test_filter_schemas_full_trust_keeps_everything() -> None:
    schemas = _openai_schemas()
    assert TrustPolicy().filter_schemas(schemas, "full") == schemas


def test_filter_schemas_limited_drops_full_only_tools() -> None:
    kept = TrustPolicy().filter_schemas(_openai_schemas(), "limited")
    names = [s["function"]["name"] for s in kept]
    assert names == ["search", "read_file"]


def test_filter_schemas_untrusted_drops_all_defaults() -> None:
    assert TrustPolicy().filter_schemas(_openai_schemas(), "untrusted") == []


def test_filter_schemas_handles_anthropic_shape() -> None:
    schemas = [
        {"name": "bash", "input_schema": {}},
        {"name": "search", "input_schema": {}},
    ]
    kept = TrustPolicy().filter_schemas(schemas, "limited")
    assert [s["name"] for s in kept] == ["search"]


def test_filter_schemas_drops_nameless_schema_below_full() -> None:
    schemas: list[dict[str, Any]] = [{"type": "function"}]
    policy = TrustPolicy()
    assert policy.filter_schemas(schemas, "limited") == []
    assert policy.filter_schemas(schemas, "full") == schemas


# ---- Runner enforcement ----------------------------------------------------


async def test_runner_denies_tool_below_required_trust() -> None:
    runner = SubprocessToolRunner(
        _registry(), trust_policy=TrustPolicy(), trust_level="limited"
    )
    result = await runner.run("echo", {"text": "hi"})
    assert result.denied and result.is_error
    assert "trust" in result.content


async def test_runner_allows_tool_at_required_trust() -> None:
    runner = SubprocessToolRunner(
        _registry(), trust_policy=TrustPolicy(), trust_level="limited"
    )
    result = await runner.run("search", {"query": "pi"})
    assert not result.denied
    assert result.content == "results for pi"


async def test_trust_denial_skips_approval_prompt() -> None:
    prompts: list[str] = []

    async def approve(name: str, args: dict[str, Any]) -> bool:
        prompts.append(name)
        return True

    runner = SubprocessToolRunner(
        _registry(),
        approval_fn=approve,
        trust_policy=TrustPolicy(),
        trust_level="untrusted",
    )
    result = await runner.run("search", {"query": "pi"})
    assert result.denied
    assert prompts == []


async def test_runner_without_policy_is_unrestricted() -> None:
    runner = SubprocessToolRunner(_registry(), trust_level="untrusted")
    result = await runner.run("echo", {"text": "hi"})
    assert result.content == "hi" and not result.denied
