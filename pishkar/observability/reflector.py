"""Reflector — periodic memory extraction from completed turns.

Subscribes to `on_turn_complete`. After every N successful turns in a
session, runs an LLM extraction pass over the recent message history
plus the existing `MEMORY.md`, asking the model to return an updated
memory document (deduped, terse, durable facts only). Result is written
back to `MEMORY.md` via the workspace loader's atomic-write path.

The reflector is fail-open: if the extraction call errors, the existing
MEMORY.md is left untouched and the agent loop is unaffected (the hook
swallows exceptions).

Triggering on completed turns rather than on a wall-clock schedule
keeps the cost proportional to actual usage — an idle butler does
nothing here.
"""

import logging
from typing import Any

from pishkar.gateway.hooks import ON_TURN_COMPLETE, HookManager
from pishkar.providers.base import ModelProvider
from pishkar.workspace.loader import WorkspaceLoader
from pishkar.workspace.store import SessionStore

logger = logging.getLogger(__name__)


_EXTRACTION_SYSTEM = (
    "You are Pishkar's memory keeper. Your job is to maintain a single "
    "markdown document of durable facts about the user, their projects, "
    "preferences, and ongoing context — distilled from recent "
    "conversation history.\n\n"
    "Rules:\n"
    "- Output the FULL updated memory document in markdown. The caller "
    "  overwrites the file with whatever you return.\n"
    "- Preserve existing facts unless a new turn explicitly contradicts "
    "  them; then update the contradicted entry, do not duplicate it.\n"
    "- Drop trivia (one-off questions, error noise, tool transcripts).\n"
    "- Keep entries terse — one sentence each, grouped under short "
    "  headers.\n"
    "- If nothing durable was learned, return the existing document "
    "  unchanged."
)


class Reflector:
    def __init__(
        self,
        *,
        provider: ModelProvider,
        model: str,
        store: SessionStore,
        loader: WorkspaceLoader,
        every_n_turns: int = 10,
        history_limit: int = 30,
    ) -> None:
        self._provider = provider
        self._model = model
        self._store = store
        self._loader = loader
        self._every_n = max(1, every_n_turns)
        self._history_limit = history_limit
        self._counts: dict[str, int] = {}
        self._busy: set[str] = set()

    def attach(self, hooks: HookManager) -> None:
        hooks.on(ON_TURN_COMPLETE, self._on_turn_complete)

    async def _on_turn_complete(
        self,
        *,
        turn_id: str,
        session_id: str | None = None,
        user_id: str | None = None,
        stop_reason: str | None = None,
        **_: Any,
    ) -> None:
        # Only reflect on cleanly-completed turns; skip errored, looped,
        # max-turn-tripped runs (likely partial / noisy).
        if stop_reason != "end_turn":
            return
        if not session_id or not user_id:
            return
        if session_id in self._busy:
            return
        n = self._counts.get(session_id, 0) + 1
        if n < self._every_n:
            self._counts[session_id] = n
            return
        self._counts[session_id] = 0
        self._busy.add(session_id)
        try:
            await self._reflect(user_id=user_id, session_id=session_id)
        except Exception:  # noqa: BLE001 — fail-open
            logger.exception(
                "reflector failed for session %s; MEMORY.md left untouched",
                session_id,
            )
        finally:
            self._busy.discard(session_id)

    async def _reflect(self, *, user_id: str, session_id: str) -> None:
        history = await self._store.session_history(session_id)
        if not history:
            return
        recent = history[-self._history_limit :]
        ws = self._loader.load(user_id)
        existing = ws.memory.strip() or "(empty — no facts yet)"

        transcript = "\n\n".join(
            f"{'User' if r['direction'] == 'inbound' else 'Pishkar'}: "
            f"{r['content']}"
            for r in recent
        )

        prompt = (
            f"## Current MEMORY.md\n\n{existing}\n\n"
            f"## Recent conversation\n\n{transcript}\n\n"
            "## Task\n\n"
            "Return the full updated MEMORY.md. Output only the document "
            "body — no preamble, no code fences."
        )

        text = await self._call(prompt)
        if not text or not text.strip():
            return
        # Keep memory sane in size; truncate at ~32 KB so a runaway
        # extraction can't bloat the system prompt indefinitely.
        if len(text) > 32_000:
            text = text[:32_000] + "\n\n[truncated]"
        self._loader.write(user_id, "MEMORY", text.strip() + "\n")

    async def _call(self, prompt: str) -> str:
        chunks: list[str] = []
        async for chunk in self._provider.stream(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            system=_EXTRACTION_SYSTEM,
        ):
            if chunk.text:
                chunks.append(chunk.text)
        return "".join(chunks)


__all__ = ["Reflector"]
