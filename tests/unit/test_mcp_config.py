"""Tests for the MCP config loader and connect-servers wiring."""

import json
from pathlib import Path
from typing import Any

import pytest

from pishkar.tools import mcp_bridge, mcp_config
from pishkar.tools.mcp_bridge import McpToolInfo
from pishkar.tools.registry import ToolRegistry


class _FakeClient:
    def __init__(
        self,
        server_name: str,
        tools: list[McpToolInfo] | None = None,
        *,
        connect_error: Exception | None = None,
        list_error: Exception | None = None,
    ) -> None:
        self.server_name = server_name
        self.tools = tools or []
        self.closed = False
        self._connect_error = connect_error
        self._list_error = list_error

    async def connect(self) -> None:
        if self._connect_error is not None:
            raise self._connect_error

    async def list_tools(self) -> list[McpToolInfo]:
        if self._list_error is not None:
            raise self._list_error
        return self.tools

    async def call_tool(self, name: str, args: dict[str, Any]) -> str:
        return f"called {name} with {args}"

    async def close(self) -> None:
        self.closed = True


def test_load_config_returns_empty_when_file_missing(tmp_path: Path) -> None:
    assert mcp_config.load_config(tmp_path / "missing.json") == {}


def test_load_config_parses_mcp_servers_block(tmp_path: Path) -> None:
    cfg = tmp_path / "mcp.json"
    cfg.write_text(json.dumps({
        "mcpServers": {
            "tavily": {"command": "npx", "args": ["-y", "tavily-mcp"]},
            "remote": {"url": "https://x.example/mcp"},
        }
    }))
    out = mcp_config.load_config(cfg)
    assert "tavily" in out and "remote" in out
    assert out["tavily"]["command"] == "npx"


def test_load_config_returns_empty_on_malformed_json(tmp_path: Path) -> None:
    cfg = tmp_path / "mcp.json"
    cfg.write_text("{ not json")
    assert mcp_config.load_config(cfg) == {}


def test_load_config_returns_empty_when_servers_missing(tmp_path: Path) -> None:
    cfg = tmp_path / "mcp.json"
    cfg.write_text(json.dumps({"some_other_key": {}}))
    assert mcp_config.load_config(cfg) == {}


async def test_connect_servers_registers_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeClient(
        "tavily",
        tools=[
            McpToolInfo(
                name="search",
                description="web search",
                input_schema={"type": "object"},
            )
        ],
    )

    def _build(name: str, _spec: dict[str, Any]) -> Any:
        assert name == "tavily"
        return fake

    monkeypatch.setattr(mcp_config, "_build_client", _build)
    registry = ToolRegistry()
    clients = await mcp_config.connect_servers(
        {"tavily": {"command": "x"}}, registry
    )
    assert clients == [fake]
    # Tool registered under qualified name.
    assert "tavily__search" in registry.names()


async def test_connect_servers_skips_failed_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad = _FakeClient("dead", connect_error=RuntimeError("nope"))
    good = _FakeClient(
        "ok",
        tools=[McpToolInfo(name="ping", input_schema={"type": "object"})],
    )

    def _build(name: str, _spec: dict[str, Any]) -> Any:
        return bad if name == "dead" else good

    monkeypatch.setattr(mcp_config, "_build_client", _build)
    registry = ToolRegistry()
    clients = await mcp_config.connect_servers(
        {"dead": {"command": "x"}, "ok": {"command": "x"}}, registry
    )
    assert clients == [good]
    assert "ok__ping" in registry.names()
    assert "dead__ping" not in registry.names()


async def test_connect_servers_closes_client_on_list_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeClient("buggy", list_error=RuntimeError("list_tools blew up"))
    monkeypatch.setattr(mcp_config, "_build_client", lambda *_a, **_k: fake)
    registry = ToolRegistry()
    clients = await mcp_config.connect_servers(
        {"buggy": {"command": "x"}}, registry
    )
    assert clients == []
    assert fake.closed is True


def test_build_client_stdio_returns_stdio_client() -> None:
    client = mcp_config._build_client(
        "tavily", {"command": "npx", "args": ["-y", "tavily-mcp"]}
    )
    assert isinstance(client, mcp_bridge.StdioMcpClient)


def test_build_client_url_returns_http_client() -> None:
    client = mcp_config._build_client(
        "remote", {"url": "https://x.example/mcp", "headers": {"k": "v"}}
    )
    assert isinstance(client, mcp_bridge.HttpStreamMcpClient)


def test_build_client_returns_none_when_neither_command_nor_url() -> None:
    assert mcp_config._build_client("bad", {"args": ["x"]}) is None


def test_build_client_stdio_seeds_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FAKE_MCP_TOKEN", raising=False)
    mcp_config._build_client(
        "x", {"command": "x", "env": {"FAKE_MCP_TOKEN": "abc"}}
    )
    import os

    assert os.environ.get("FAKE_MCP_TOKEN") == "abc"


async def test_disconnect_servers_closes_all() -> None:
    a = _FakeClient("a")
    b = _FakeClient("b")
    await mcp_config.disconnect_servers([a, b])
    assert a.closed and b.closed


async def test_disconnect_swallows_exceptions() -> None:
    class Boom:
        server_name = "boom"

        async def close(self) -> None:
            raise RuntimeError("won't close")

    # Must not raise.
    await mcp_config.disconnect_servers([Boom()])  # type: ignore[list-item]
