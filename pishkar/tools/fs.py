"""Filesystem tools: `read_file`, `write_file`.

Writes go through `atomic_write_text` so a crash mid-write cannot corrupt
the target. Both tools return plain strings — `ToolRunner` (item 8)
applies the max-result-size cap.
"""

from __future__ import annotations

from pathlib import Path

from pishkar.tools.registry import tool
from pishkar.workspace.atomic_io import atomic_write_text


@tool(description="Read a UTF-8 text file and return its contents.")
async def read_file(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


@tool(description="Atomically write UTF-8 text to a file (creates parent dirs).")
async def write_file(path: str, content: str) -> str:
    target = Path(path)
    atomic_write_text(target, content)
    return f"wrote {len(content)} bytes to {target}"


__all__ = ["read_file", "write_file"]
