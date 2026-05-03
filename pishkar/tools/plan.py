"""`plan` tool — write a structured task plan and persist it per session.

The model uses this for any task that needs more than a couple of tool
calls. The plan goes into history (so the model sees it on subsequent
turns) AND gets atomically written to
`~/.pishkar/users/<user_id>/plans/<session_id>.md` so it survives
context compaction and is available across user messages within the
same session.

Calling `plan` again with new content overwrites the file — that's the
revision pattern. Two competing plans for one session are not a useful
shape; the latest call wins.
"""

from pathlib import Path

from pishkar.core.context import current_session_id, current_user_id
from pishkar.tools.registry import tool
from pishkar.workspace.atomic_io import atomic_write_text

_BASE_DIR = Path.home() / ".pishkar"


def _plan_path(user_id: str, session_id: str) -> Path:
    return _BASE_DIR / "users" / user_id / "plans" / f"{session_id}.md"


@tool(
    description=(
        "Record a step-by-step plan for the current task. Use a markdown "
        "checklist (- [ ] step …). Call again to revise as you learn things. "
        "The plan is saved per-session and visible to you on subsequent turns."
    )
)
async def plan(content: str) -> str:
    user_id = current_user_id.get()
    session_id = current_session_id.get()
    if user_id and session_id:
        path = _plan_path(user_id, session_id)
        atomic_write_text(path, content)
        return f"Plan saved to {path}.\n\n{content}"
    # Outside a turn (CLI / test) — just echo the plan back.
    return content


__all__ = ["plan"]
