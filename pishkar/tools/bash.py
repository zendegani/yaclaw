"""`bash` tool — runs a shell command and returns combined stdout/stderr.

This is the raw implementation. `ToolRunner` wraps it with the 30-second
default timeout and the 1 MB max-result cap. The approval gate decides
whether to invoke at all. The tool function itself is intentionally thin
so the runner can compose policies around it.
"""

import asyncio

from pishkar.tools.registry import tool


@tool(description="Run a shell command via /bin/sh and return its combined output.")
async def bash(cmd: str) -> str:
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    text = out.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        return f"[exit {proc.returncode}]\n{text}"
    return text


__all__ = ["bash"]
