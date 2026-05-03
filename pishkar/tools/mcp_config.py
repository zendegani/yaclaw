"""Load and connect MCP servers declared in `~/.pishkar/mcp_servers.json`.

Config format mirrors Claude Desktop / Cursor / Continue so existing
server entries can be copied over verbatim:

```json
{
  "mcpServers": {
    "tavily":   {"command": "npx", "args": ["-y", "tavily-mcp"],
                 "env": {"TAVILY_API_KEY": "..."}},
    "remote":   {"url": "https://mcp.example.com/stream",
                 "headers": {"Authorization": "Bearer ..."}}
  }
}
```

Stdio entries provide `command` (+ optional `args`, `env`); HTTP-stream
entries provide `url` (+ optional `headers`). Anything else is ignored.

Connection is fail-open: a server that won't start is logged and
skipped, never aborts Pishkar's boot. Each successful connect registers
the server's tools into the shared `ToolRegistry` under qualified names
(`<server>__<tool>`) so collisions are impossible across servers.
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from pishkar.tools.mcp_bridge import (
    HttpStreamMcpClient,
    McpClient,
    StdioMcpClient,
    register_mcp_tools,
)
from pishkar.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path.home() / ".pishkar" / "mcp_servers.json"
DEFAULT_CONNECT_TIMEOUT_S = 15.0


def load_config(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Read the JSON config and return the `mcpServers` map.

    Returns an empty dict when the file is missing — MCP is opt-in by
    presence of the file rather than an env flag, matching how Claude
    Desktop / Cursor work."""
    target = path or DEFAULT_CONFIG_PATH
    if not target.is_file():
        return {}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("could not read MCP config %s: %s", target, e)
        return {}
    servers = raw.get("mcpServers")
    if not isinstance(servers, dict):
        logger.warning(
            "MCP config %s missing 'mcpServers' object; ignoring", target
        )
        return {}
    return servers


def _build_client(name: str, spec: dict[str, Any]) -> McpClient | None:
    if "command" in spec:
        env = spec.get("env")
        if isinstance(env, dict):
            # Stdio servers inherit env from os.environ; merge in spec env
            # before the connect spawn so server-specific keys (API tokens
            # etc.) reach the child without leaking into Pishkar's own env.
            for k, v in env.items():
                os.environ.setdefault(str(k), str(v))
        args = spec.get("args")
        if not isinstance(args, list):
            args = []
        return StdioMcpClient(server_name=name, command=spec["command"], args=args)
    if "url" in spec:
        headers = spec.get("headers")
        if not isinstance(headers, dict):
            headers = {}
        return HttpStreamMcpClient(server_name=name, url=spec["url"], headers=headers)
    logger.warning(
        "MCP server %r has neither 'command' nor 'url'; skipping", name
    )
    return None


async def connect_servers(
    servers: dict[str, dict[str, Any]],
    registry: ToolRegistry,
    *,
    connect_timeout_s: float = DEFAULT_CONNECT_TIMEOUT_S,
) -> list[McpClient]:
    """Connect each configured server and register its tools into `registry`.

    Returns the connected clients so the caller can close them on
    shutdown. Failures are logged and skipped — Pishkar boots with the
    servers that did come up. Each connect is bounded by
    `connect_timeout_s` so a hung server can't stall the runtime."""
    connected: list[McpClient] = []
    for name, spec in servers.items():
        client = _build_client(name, spec)
        if client is None:
            continue
        try:
            await asyncio.wait_for(client.connect(), timeout=connect_timeout_s)
        except Exception:  # noqa: BLE001 — log and continue
            logger.exception("MCP server %r failed to connect; skipping", name)
            continue
        try:
            specs = await register_mcp_tools(client, registry)
        except Exception:  # noqa: BLE001
            logger.exception(
                "MCP server %r connected but list_tools failed; closing", name
            )
            with _suppress():
                await client.close()
            continue
        connected.append(client)
        logger.info(
            "MCP server %r registered %d tool(s): %s",
            name, len(specs), ", ".join(s.name for s in specs),
        )
    return connected


async def disconnect_servers(clients: list[McpClient]) -> None:
    """Close each client; swallow exceptions so a hung server can't block
    shutdown of the rest."""
    for client in clients:
        with _suppress():
            await client.close()


class _suppress:
    """Tiny stand-in for `contextlib.suppress(Exception)` in async paths
    where we also want to log the exception type at debug level."""

    def __enter__(self) -> _suppress:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:  # type: ignore[no-untyped-def]
        if exc is not None:
            logger.debug("suppressed during MCP teardown: %s", exc)
        return True


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "connect_servers",
    "disconnect_servers",
    "load_config",
]
