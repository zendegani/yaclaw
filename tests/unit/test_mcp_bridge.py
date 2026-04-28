from typing import Any

import pytest

from pishkar.tools.mcp_bridge import (
    McpClient,
    McpToolInfo,
    register_mcp_tools,
)
from pishkar.tools.registry import ToolRegistry
from pishkar.tools.runner import SubprocessToolRunner


class FakeMcpClient:
    server_name = "weather"

    def __init__(self, tools: list[McpToolInfo]) -> None:
        self._tools = tools
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    async def list_tools(self) -> list[McpToolInfo]:
        return self._tools

    async def call_tool(self, name: str, args: dict[str, Any]) -> str:
        self.calls.append((name, args))
        return f"{name}:{args}"

    async def close(self) -> None:
        self.closed = True


def _info(name: str, **schema_props: Any) -> McpToolInfo:
    return McpToolInfo(
        name=name,
        description=f"{name} from MCP",
        input_schema={
            "type": "object",
            "properties": schema_props or {"q": {"type": "string"}},
            "required": list(schema_props) or ["q"],
        },
    )


def test_fake_client_satisfies_protocol() -> None:
    assert isinstance(FakeMcpClient([]), McpClient)


async def test_register_namespaces_tools_with_server_name() -> None:
    client = FakeMcpClient([_info("forecast"), _info("alerts")])
    reg = ToolRegistry()
    specs = await register_mcp_tools(client, reg)

    assert {s.name for s in specs} == {"weather__forecast", "weather__alerts"}
    assert set(reg.names()) == {"weather__forecast", "weather__alerts"}


async def test_registered_tool_preserves_remote_input_schema() -> None:
    info = _info("forecast", city={"type": "string"}, units={"type": "string"})
    client = FakeMcpClient([info])
    reg = ToolRegistry()
    await register_mcp_tools(client, reg)

    spec = reg.get("weather__forecast")
    assert spec.input_schema == info.input_schema
    assert spec.description == "forecast from MCP"


async def test_registered_proxy_forwards_call_to_client() -> None:
    client = FakeMcpClient([_info("forecast", city={"type": "string"})])
    reg = ToolRegistry()
    await register_mcp_tools(client, reg)

    out = await reg.call("weather__forecast", {"city": "Berlin"})
    assert client.calls == [("forecast", {"city": "Berlin"})]
    assert "Berlin" in out


async def test_proxy_works_through_runner_with_timeout_and_cap() -> None:
    client = FakeMcpClient([_info("ping")])
    reg = ToolRegistry()
    await register_mcp_tools(client, reg)

    runner = SubprocessToolRunner(reg, default_max_bytes=10_000)
    result = await runner.run("weather__ping", {"q": "hi"})
    assert not result.is_error
    assert "hi" in result.content


async def test_two_servers_can_share_tool_names() -> None:
    a = FakeMcpClient([_info("search")])
    a.server_name = "google"
    b = FakeMcpClient([_info("search")])
    b.server_name = "duckduckgo"

    reg = ToolRegistry()
    await register_mcp_tools(a, reg)
    await register_mcp_tools(b, reg)
    assert set(reg.names()) == {"google__search", "duckduckgo__search"}


async def test_passthrough_args_model_accepts_unknown_fields() -> None:
    client = FakeMcpClient([_info("forecast", city={"type": "string"})])
    reg = ToolRegistry()
    await register_mcp_tools(client, reg)
    # Server schema validation lives on the MCP side; bridge should not reject.
    out = await reg.call("weather__forecast", {"city": "X", "extra": "ignored"})
    assert client.calls == [("forecast", {"city": "X", "extra": "ignored"})]
    assert "X" in out


async def test_concrete_clients_require_mcp_sdk() -> None:
    from pishkar.tools.mcp_bridge import StdioMcpClient

    c = StdioMcpClient(server_name="x", command="echo")
    pytest.importorskip("mcp", reason="mcp SDK not installed; transport is lazy-imported")
    # If mcp is available, just check that connect is callable without raising
    # an ImportError at attribute access.
    assert callable(c.connect)
