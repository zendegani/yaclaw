"""`search_memory` tool — semantic recall over past conversations.

The tool is registered unconditionally so the model can discover it;
without a configured `MemoryIndex` (no `PISHKAR_EMBEDDING_MODEL`) it
explains how to enable the feature instead of failing. `configure()` is
called by the server wiring, mirroring how the voice dispatcher gets
its runtime state.
"""

from pishkar.core.context import current_user_id
from pishkar.tools.registry import tool
from pishkar.workspace.memory import MemoryIndex

_SNIPPET_CHARS = 300

_index: MemoryIndex | None = None


def configure(index: MemoryIndex | None) -> None:
    global _index
    _index = index


@tool(
    description=(
        "Search past conversations semantically. Use when the user refers "
        "to something discussed before ('that restaurant we talked about', "
        "'my flight details') that isn't in the current context. Returns "
        "the closest matching messages with timestamps."
    )
)
async def search_memory(query: str, k: int = 5) -> str:
    if _index is None:
        return (
            "Semantic memory is not configured. Set PISHKAR_EMBEDDING_MODEL "
            "in .env (any litellm embedding model) to enable it."
        )
    k = max(1, min(k, 20))
    results = await _index.search(query, k=k, user_id=current_user_id.get() or None)
    if not results:
        return "No matching messages found."
    lines = []
    for r in results:
        speaker = "User" if r["direction"] == "inbound" else "Pishkar"
        content = str(r["content"]).strip().replace("\n", " ")
        if len(content) > _SNIPPET_CHARS:
            content = content[:_SNIPPET_CHARS] + "…"
        lines.append(f"[{r['timestamp']}] {speaker}: {content}")
    return "\n".join(lines)


__all__ = ["configure", "search_memory"]
