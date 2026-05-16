"""HeartbeatTrigger — fires scheduled prompts from `cron.json`.

State is kept in `cron.json` itself: each task carries `last_fired_at`
which is bumped after a successful submit. This makes startup catch-up
free — a task whose `last_fired_at` is older than its interval is
"due", whether the gap was a single tick or a multi-day downtime. The
synthetic `InboundMessage` carries `was_due_at` in `metadata` so the
agent can choose to act now, skip as stale, or batch.

`HEARTBEAT.md`, when present, is attached to message metadata as
free-form context the agent can consult — it is not parsed by the
trigger. The split is deliberate: scheduling is structured (cron.json),
intent is prose (HEARTBEAT.md).

Cron entry shape:

    {
      "id": "morning-brief",
      "user_id": "ali",
      "session_id": "s-default",
      "interval_seconds": 86400,
      "prompt": "Run my morning briefing",
      "last_fired_at": null            # set by the trigger
    }
"""

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pishkar.core.messages import InboundMessage
from pishkar.triggers.base import Submit
from pishkar.workspace.atomic_io import atomic_write_text


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _parse_iso(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class HeartbeatTrigger:
    name = "heartbeat"

    def __init__(
        self,
        *,
        cron_path: Path,
        heartbeat_path: Path | None = None,
        tick_interval: float = 60.0,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._cron_path = Path(cron_path)
        self._heartbeat_path = Path(heartbeat_path) if heartbeat_path else None
        self._tick_interval = tick_interval
        self._now = now or _utcnow
        self._stopped = asyncio.Event()

    async def run(self, submit: Submit) -> None:
        while not self._stopped.is_set():
            await self.tick(submit)
            try:
                await asyncio.wait_for(
                    self._stopped.wait(), timeout=self._tick_interval
                )
            except TimeoutError:
                continue

    async def stop(self) -> None:
        self._stopped.set()

    async def tick(self, submit: Submit) -> int:
        """One scheduling pass. Returns count of messages submitted."""
        tasks = self._load_cron()
        if not tasks:
            return 0
        heartbeat_text = self._read_heartbeat()
        now = self._now()
        fired = 0

        for task in tasks:
            interval = task.get("interval_seconds")
            if not isinstance(interval, int) or interval <= 0:
                continue
            last = _parse_iso(task.get("last_fired_at"))
            if last is not None and (now - last).total_seconds() < interval:
                continue
            was_due_at = (
                (last + timedelta(seconds=interval)).isoformat()
                if last is not None
                else now.isoformat()
            )
            metadata: dict[str, Any] = {
                "trigger_id": task.get("id"),
                "was_due_at": was_due_at,
            }
            if heartbeat_text:
                metadata["heartbeat_context"] = heartbeat_text
            await submit(
                InboundMessage(
                    user_id=task["user_id"],
                    session_id=task["session_id"],
                    channel=self.name,
                    content=task["prompt"],
                    metadata=metadata,
                )
            )
            task["last_fired_at"] = now.isoformat()
            fired += 1

        if fired:
            self._save_cron(tasks)
        return fired

    # ---- file IO ---------------------------------------------------------

    def _load_cron(self) -> list[dict[str, Any]]:
        if not self._cron_path.is_file():
            return []
        try:
            data = json.loads(self._cron_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return data if isinstance(data, list) else []

    def _save_cron(self, tasks: list[dict[str, Any]]) -> None:
        atomic_write_text(self._cron_path, json.dumps(tasks, indent=2))

    def _read_heartbeat(self) -> str:
        if self._heartbeat_path is None or not self._heartbeat_path.is_file():
            return ""
        try:
            return self._heartbeat_path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""


__all__ = ["HeartbeatTrigger"]
