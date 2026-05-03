"""SQLite-backed session store.

Single shared DB. Tables: `sessions`, `messages`, `turns`, `tool_calls`,
`tool_results`, `governance_decisions`, `token_spend`. Resilience seams
baked in from day one:

* `messages.delivered_at` → SQLite-backed inbound queue.
* `tool_calls.tool_call_id` UUID + status, with `tool_call` and `tool_result`
  committed in one transaction. Orphaned `tool_call` rows are marked
  `interrupted` on startup.
* `turns.started_at` / `ended_at` → mid-turn crash detection: any turn
  with `started_at` but no `ended_at` was interrupted.

"Append-only" is the spirit, not the literal: we mutate a small set of
state-machine columns (`delivered_at`, `turns.ended_at`,
`tool_calls.status`). Everything else is insert-only.
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiosqlite

from pishkar.core.messages import InboundMessage, OutboundMessage, Session, TrustLevel

ToolCallStatus = str  # 'pending' | 'completed' | 'error' | 'interrupted'


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id  TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    message_id    TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL,
    user_id       TEXT NOT NULL,
    channel       TEXT NOT NULL,
    direction     TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    content       TEXT NOT NULL,
    trust_level   TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    timestamp     TEXT NOT NULL,
    delivered_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_session_time
    ON messages(session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_undelivered
    ON messages(timestamp)
    WHERE direction = 'inbound' AND delivered_at IS NULL;

CREATE TABLE IF NOT EXISTS turns (
    turn_id     TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    stop_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id);

CREATE TABLE IF NOT EXISTS tool_calls (
    tool_call_id TEXT PRIMARY KEY,
    turn_id      TEXT NOT NULL,
    tool_name    TEXT NOT NULL,
    args_json    TEXT NOT NULL,
    args_hash    TEXT NOT NULL,
    status       TEXT NOT NULL
                 CHECK (status IN ('pending', 'completed', 'error', 'interrupted')),
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tool_calls_turn ON tool_calls(turn_id);

CREATE TABLE IF NOT EXISTS tool_results (
    tool_call_id TEXT PRIMARY KEY REFERENCES tool_calls(tool_call_id),
    content      TEXT NOT NULL,
    is_error     INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS governance_decisions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          TEXT NOT NULL,
    session_id       TEXT,
    tool_name        TEXT NOT NULL,
    tool_call_id     TEXT,
    decision         TEXT NOT NULL,
    scope            TEXT NOT NULL,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS token_spend (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       TEXT NOT NULL,
    model         TEXT NOT NULL,
    input_tokens  INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_usd      REAL,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_token_spend_user_time
    ON token_spend(user_id, created_at);

CREATE TABLE IF NOT EXISTS user_prefs (
    user_id    TEXT NOT NULL,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);

CREATE TABLE IF NOT EXISTS events (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     TEXT NOT NULL UNIQUE,
    turn_id      TEXT,
    session_id   TEXT,
    type         TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_session_seq
    ON events(session_id, seq);
"""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def args_hash(args: dict[str, Any]) -> str:
    """SHA256 of canonicalized args. Used by loop guard (core/loop_guard.py)."""
    canonical = json.dumps(args, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class SessionStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        if self._db is not None:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(_SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def __aenter__(self) -> SessionStore:
        await self.open()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("SessionStore is not open; call .open() or use async with")
        return self._db

    # --- Sessions ---------------------------------------------------------

    async def create_session(self, user_id: str, session_id: str | None = None) -> Session:
        session = Session(session_id=session_id or str(uuid4()), user_id=user_id)
        await self.db.execute(
            "INSERT INTO sessions(session_id, user_id, created_at) VALUES (?, ?, ?)",
            (session.session_id, session.user_id, session.created_at.isoformat()),
        )
        await self.db.commit()
        return session

    async def get_session(self, session_id: str) -> Session | None:
        async with self.db.execute(
            "SELECT session_id, user_id, created_at FROM sessions WHERE session_id = ?",
            (session_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return Session(
            session_id=row[0], user_id=row[1], created_at=datetime.fromisoformat(row[2])
        )

    # --- Messages (also: SQLite-backed inbound queue) ---------------------

    async def enqueue_inbound(self, msg: InboundMessage) -> None:
        await self.db.execute(
            """INSERT INTO messages(message_id, session_id, user_id, channel,
                                    direction, content, trust_level, metadata_json,
                                    timestamp, delivered_at)
               VALUES (?, ?, ?, ?, 'inbound', ?, ?, ?, ?, NULL)""",
            (
                msg.message_id,
                msg.session_id,
                msg.user_id,
                msg.channel,
                msg.content,
                msg.trust_level,
                json.dumps(msg.metadata),
                msg.timestamp.isoformat(),
            ),
        )
        await self.db.commit()

    async def fetch_undelivered_inbound(self) -> list[InboundMessage]:
        async with self.db.execute(
            """SELECT message_id, session_id, user_id, channel, content,
                      trust_level, metadata_json, timestamp
               FROM messages
               WHERE direction = 'inbound' AND delivered_at IS NULL
               ORDER BY timestamp ASC"""
        ) as cur:
            rows = await cur.fetchall()
        return [
            InboundMessage(
                message_id=r[0],
                session_id=r[1],
                user_id=r[2],
                channel=r[3],
                content=r[4],
                trust_level=r[5] or "full",
                metadata=json.loads(r[6]),
                timestamp=datetime.fromisoformat(r[7]),
            )
            for r in rows
        ]

    async def mark_delivered(self, message_id: str) -> None:
        await self.db.execute(
            "UPDATE messages SET delivered_at = ? WHERE message_id = ?",
            (_now_iso(), message_id),
        )
        await self.db.commit()

    async def record_outbound(self, msg: OutboundMessage) -> None:
        await self.db.execute(
            """INSERT INTO messages(message_id, session_id, user_id, channel,
                                    direction, content, trust_level, metadata_json,
                                    timestamp, delivered_at)
               VALUES (?, ?, ?, ?, 'outbound', ?, NULL, ?, ?, ?)""",
            (
                msg.message_id,
                msg.session_id,
                msg.user_id,
                msg.channel,
                msg.content,
                json.dumps(msg.metadata),
                msg.timestamp.isoformat(),
                msg.timestamp.isoformat(),
            ),
        )
        await self.db.commit()

    async def latest_session_for_user(self, user_id: str) -> str | None:
        """Session id of the user's most recent activity. Considers both
        the `sessions` table (so an empty just-created session counts) and
        the `messages` table (so legacy sessions without an explicit row
        still resolve)."""
        async with self.db.execute(
            """SELECT session_id, MAX(t) AS t FROM (
                   SELECT s.session_id, s.created_at AS t
                   FROM sessions s WHERE s.user_id = ?
                   UNION ALL
                   SELECT m.session_id, MAX(m.timestamp) AS t
                   FROM messages m WHERE m.user_id = ?
                   GROUP BY m.session_id
               ) GROUP BY session_id ORDER BY t DESC LIMIT 1""",
            (user_id, user_id),
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row else None

    async def recent_sessions_for_user(
        self, user_id: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Recent sessions for the user, newest first. Each entry: session_id,
        created_at, last_activity, message_count, last_channel."""
        async with self.db.execute(
            """SELECT s.session_id, s.created_at,
                      COALESCE(MAX(m.timestamp), s.created_at) AS last_activity,
                      COUNT(m.message_id) AS message_count,
                      (SELECT channel FROM messages
                       WHERE session_id = s.session_id
                       ORDER BY timestamp DESC LIMIT 1) AS last_channel
               FROM sessions s
               LEFT JOIN messages m ON m.session_id = s.session_id
               WHERE s.user_id = ?
               GROUP BY s.session_id
               ORDER BY last_activity DESC
               LIMIT ?""",
            (user_id, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [
            {
                "session_id": r[0],
                "created_at": r[1],
                "last_activity": r[2],
                "message_count": int(r[3]),
                "last_channel": r[4],
            }
            for r in rows
        ]

    async def session_history(self, session_id: str) -> list[dict[str, Any]]:
        async with self.db.execute(
            """SELECT message_id, direction, content, channel, timestamp, metadata_json
               FROM messages
               WHERE session_id = ?
               ORDER BY timestamp ASC""",
            (session_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [
            {
                "message_id": r[0],
                "direction": r[1],
                "content": r[2],
                "channel": r[3],
                "timestamp": r[4],
                "metadata": json.loads(r[5]),
            }
            for r in rows
        ]

    # --- Turns (mid-turn crash detection) ---------------------------------

    async def start_turn(self, turn_id: str, session_id: str) -> None:
        await self.db.execute(
            "INSERT INTO turns(turn_id, session_id, started_at) VALUES (?, ?, ?)",
            (turn_id, session_id, _now_iso()),
        )
        await self.db.commit()

    async def end_turn(self, turn_id: str, stop_reason: str) -> None:
        await self.db.execute(
            "UPDATE turns SET ended_at = ?, stop_reason = ? WHERE turn_id = ?",
            (_now_iso(), stop_reason, turn_id),
        )
        await self.db.commit()

    async def find_interrupted_turns(self, session_id: str | None = None) -> list[str]:
        if session_id is None:
            sql = "SELECT turn_id FROM turns WHERE ended_at IS NULL ORDER BY started_at"
            params: tuple[Any, ...] = ()
        else:
            sql = (
                "SELECT turn_id FROM turns WHERE ended_at IS NULL AND session_id = ? "
                "ORDER BY started_at"
            )
            params = (session_id,)
        async with self.db.execute(sql, params) as cur:
            return [row[0] for row in await cur.fetchall()]

    # --- Tool calls (transactional) ---------------------------------------

    async def record_tool_call(
        self,
        tool_call_id: str,
        turn_id: str,
        tool_name: str,
        args: dict[str, Any],
    ) -> None:
        """Record a pending tool call. Caller commits the result via
        `complete_tool_call` in one transaction with the call's status flip."""
        await self.db.execute(
            """INSERT INTO tool_calls(tool_call_id, turn_id, tool_name, args_json,
                                       args_hash, status, created_at)
               VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
            (
                tool_call_id,
                turn_id,
                tool_name,
                json.dumps(args, sort_keys=True),
                args_hash(args),
                _now_iso(),
            ),
        )
        await self.db.commit()

    async def complete_tool_call(
        self, tool_call_id: str, content: str, is_error: bool = False
    ) -> None:
        """Atomically write the tool_result row and flip the tool_call status."""
        status = "error" if is_error else "completed"
        now = _now_iso()
        try:
            await self.db.execute("BEGIN")
            await self.db.execute(
                """INSERT INTO tool_results(tool_call_id, content, is_error, created_at)
                   VALUES (?, ?, ?, ?)""",
                (tool_call_id, content, 1 if is_error else 0, now),
            )
            await self.db.execute(
                "UPDATE tool_calls SET status = ? WHERE tool_call_id = ?",
                (status, tool_call_id),
            )
            await self.db.commit()
        except BaseException:
            await self.db.rollback()
            raise

    async def mark_orphan_tool_calls_interrupted(self) -> int:
        """Tag any pending tool_call without a result as `interrupted`.

        Run on startup to recover from a crash mid-tool-call."""
        cur = await self.db.execute(
            """UPDATE tool_calls SET status = 'interrupted'
               WHERE status = 'pending'
                 AND tool_call_id NOT IN (SELECT tool_call_id FROM tool_results)"""
        )
        await self.db.commit()
        return cur.rowcount

    # --- Governance decisions (approval-gate audit) -----------------------

    async def record_governance_decision(
        self,
        user_id: str,
        tool_name: str,
        decision: str,
        scope: str,
        session_id: str | None = None,
        tool_call_id: str | None = None,
    ) -> None:
        await self.db.execute(
            """INSERT INTO governance_decisions(user_id, session_id, tool_name,
                                                tool_call_id, decision, scope, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, session_id, tool_name, tool_call_id, decision, scope, _now_iso()),
        )
        await self.db.commit()

    # --- Token spend ------------------------------------------------------

    async def record_token_spend(
        self,
        user_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float | None = None,
    ) -> None:
        await self.db.execute(
            """INSERT INTO token_spend(user_id, model, input_tokens, output_tokens,
                                       cost_usd, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, model, input_tokens, output_tokens, cost_usd, _now_iso()),
        )
        await self.db.commit()

    async def tokens_spent_since(self, user_id: str, since_iso: str) -> tuple[int, int]:
        async with self.db.execute(
            """SELECT COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0)
               FROM token_spend
               WHERE user_id = ? AND created_at >= ?""",
            (user_id, since_iso),
        ) as cur:
            row = await cur.fetchone()
        return (int(row[0]), int(row[1])) if row else (0, 0)

    async def tokens_spent_by_model_since(
        self, user_id: str, since_iso: str
    ) -> list[tuple[str, int, int]]:
        """Return [(model, input_tokens, output_tokens), …] grouped by model
        for the given user since `since_iso`. Used by `/usage` to render a
        per-model breakdown."""
        async with self.db.execute(
            """SELECT model,
                      COALESCE(SUM(input_tokens), 0),
                      COALESCE(SUM(output_tokens), 0)
               FROM token_spend
               WHERE user_id = ? AND created_at >= ?
               GROUP BY model
               ORDER BY SUM(input_tokens + output_tokens) DESC""",
            (user_id, since_iso),
        ) as cur:
            rows = await cur.fetchall()
        return [(str(r[0]), int(r[1]), int(r[2])) for r in rows]

    # --- User preferences (key/value per user) ----------------------------

    async def get_pref(self, user_id: str, key: str) -> str | None:
        async with self.db.execute(
            "SELECT value FROM user_prefs WHERE user_id = ? AND key = ?",
            (user_id, key),
        ) as cur:
            row = await cur.fetchone()
        return str(row[0]) if row else None

    async def set_pref(self, user_id: str, key: str, value: str) -> None:
        await self.db.execute(
            """INSERT INTO user_prefs(user_id, key, value, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id, key) DO UPDATE
                 SET value = excluded.value, updated_at = excluded.updated_at""",
            (user_id, key, value, _now_iso()),
        )
        await self.db.commit()

    # --- Event audit log (also: WebSocket reconnect replay) ----------------

    async def append_event(
        self,
        *,
        event_id: str,
        type: str,
        payload_json: str,
        turn_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        await self.db.execute(
            """INSERT OR IGNORE INTO events(event_id, turn_id, session_id, type,
                                            payload_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (event_id, turn_id, session_id, type, payload_json, _now_iso()),
        )
        await self.db.commit()

    async def events_after(
        self, session_id: str, after_event_id: str | None = None
    ) -> list[dict[str, Any]]:
        if after_event_id is None:
            sql = (
                "SELECT event_id, turn_id, session_id, type, payload_json, created_at "
                "FROM events WHERE session_id = ? ORDER BY seq ASC"
            )
            params: tuple[Any, ...] = (session_id,)
        else:
            sql = (
                "SELECT event_id, turn_id, session_id, type, payload_json, created_at "
                "FROM events WHERE session_id = ? AND seq > "
                "(SELECT seq FROM events WHERE event_id = ?) ORDER BY seq ASC"
            )
            params = (session_id, after_event_id)
        async with self.db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [
            {
                "event_id": r[0],
                "turn_id": r[1],
                "session_id": r[2],
                "type": r[3],
                "payload_json": r[4],
                "created_at": r[5],
            }
            for r in rows
        ]


__all__ = ["SessionStore", "TrustLevel", "args_hash"]
