"""MCP bridge — registers tools exposed by an MCP server into `ToolRegistry`.

Each connected MCP client contributes one or more tools. The bridge calls
`list_tools()` once at startup, builds an `args_model` from the
JSON-schema `inputSchema`, and registers a thin proxy function that
forwards the call back to the client.

MCP servers are *trusted extensions*: they ran the install. The
`SubprocessToolRunner` sandboxing applied to `bash` is intentionally not
applied here (the operator is who installed the server in the first
place; double-sandboxing breaks long-lived stdio handshakes). Timeout +
result cap still apply.

`StdioMcpClient` and `HttpStreamMcpClient` lazily import the `mcp` SDK
so unit tests inject a fake client implementing the small `McpClient`
Protocol.
"""

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from pishkar.tools.registry import ToolRegistry, ToolSpec


class McpToolInfo(BaseModel):
    name: str
    description: str = ""
    input_schema: dict[str, Any]


@runtime_checkable
class McpClient(Protocol):
    server_name: str

    async def connect(self) -> None: ...
    async def list_tools(self) -> list[McpToolInfo]: ...
    async def call_tool(self, name: str, args: dict[str, Any]) -> str: ...
    async def close(self) -> None: ...


class _PassthroughArgs(BaseModel):
    """Permissive pydantic model for MCP tools — we trust the server's
    own JSON-schema validation rather than re-deriving a Python class."""

    model_config = ConfigDict(extra="allow")


def _qualified_name(server_name: str, tool_name: str) -> str:
    return f"{server_name}__{tool_name}"


def _make_proxy(
    client: McpClient, tool_name: str
) -> Callable[..., Awaitable[str]]:
    async def proxy(**kwargs: Any) -> str:
        return await client.call_tool(tool_name, kwargs)

    proxy.__name__ = tool_name
    return proxy


async def register_mcp_tools(
    client: McpClient, registry: ToolRegistry
) -> list[ToolSpec]:
    """List tools on the client and register each into `registry`.

    Returns the list of ToolSpecs registered (one per remote tool)."""
    infos = await client.list_tools()
    specs: list[ToolSpec] = []
    for info in infos:
        qualified = _qualified_name(client.server_name, info.name)
        proxy = _make_proxy(client, info.name)
        spec = ToolSpec(
            name=qualified,
            description=info.description,
            input_schema=info.input_schema,
            args_model=_PassthroughArgs,
            func=proxy,
        )
        proxy._tool_spec = spec  # type: ignore[attr-defined]
        registry.register(proxy)
        specs.append(spec)
    return specs


class StdioMcpClient:
    """Adapter over the `mcp` SDK's stdio transport.

    Lazy-imports `mcp` so this module stays import-clean when MCP isn't
    wired up. Construct with `command` + `args`; `connect()` spawns the
    server process; `close()` shuts it down.
    """

    def __init__(self, server_name: str, command: str, args: list[str] | None = None) -> None:
        self.server_name = server_name
        self._command = command
        self._args = args or []
        self._session: Any = None
        self._stack: Any = None

    async def connect(self) -> None:
        from contextlib import AsyncExitStack

        from mcp import ClientSession, StdioServerParameters  # type: ignore[import-not-found]
        from mcp.client.stdio import stdio_client  # type: ignore[import-not-found]

        self._stack = AsyncExitStack()
        params = StdioServerParameters(command=self._command, args=self._args)
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()

    async def list_tools(self) -> list[McpToolInfo]:
        result = await self._session.list_tools()
        return [
            McpToolInfo(
                name=t.name,
                description=t.description or "",
                input_schema=t.inputSchema or {},
            )
            for t in result.tools
        ]

    async def call_tool(self, name: str, args: dict[str, Any]) -> str:
        result = await self._session.call_tool(name, arguments=args)
        return "\n".join(getattr(c, "text", repr(c)) for c in result.content)

    async def close(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None
            self._session = None


class HttpStreamMcpClient:
    """Adapter over the MCP HTTP-stream transport.

    Same shape as `StdioMcpClient`. `mcp` SDK ships an HTTP-stream client
    under `mcp.client.streamable_http`."""

    def __init__(self, server_name: str, url: str, headers: dict[str, str] | None = None) -> None:
        self.server_name = server_name
        self._url = url
        self._headers = headers or {}
        self._session: Any = None
        self._stack: Any = None

    async def connect(self) -> None:
        from contextlib import AsyncExitStack

        from mcp import ClientSession
        from mcp.client.streamable_http import (  # type: ignore[import-not-found]
            streamablehttp_client,
        )

        self._stack = AsyncExitStack()
        read, write, _ = await self._stack.enter_async_context(
            streamablehttp_client(self._url, headers=self._headers)
        )
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()

    async def list_tools(self) -> list[McpToolInfo]:
        result = await self._session.list_tools()
        return [
            McpToolInfo(
                name=t.name,
                description=t.description or "",
                input_schema=t.inputSchema or {},
            )
            for t in result.tools
        ]

    async def call_tool(self, name: str, args: dict[str, Any]) -> str:
        result = await self._session.call_tool(name, arguments=args)
        return "\n".join(getattr(c, "text", repr(c)) for c in result.content)

    async def close(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None
            self._session = None


__all__ = [
    "HttpStreamMcpClient",
    "McpClient",
    "McpToolInfo",
    "StdioMcpClient",
    "register_mcp_tools",
]
