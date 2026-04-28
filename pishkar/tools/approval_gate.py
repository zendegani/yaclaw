"""Approval gate.

Three answers per prompt: *Ask Me* (one-shot), *Allow Once* (run this
call), *Allow All This Session* (remember the tool name for the rest of
the session). The gate is injected into `SubprocessToolRunner` as its
`approval_fn`. The same gate is reused by both the CLI prompt and the
WebSocket approval dialog — only the `prompt_fn` differs.

Decisions are written to `governance_decisions` via the optional
`SessionStore`, providing an after-the-fact audit trail.
"""

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from enum import Enum
from typing import Any

from pishkar.workspace.store import SessionStore


class ApprovalDecision(str, Enum):
    ALLOW_ONCE = "allow_once"
    ALLOW_SESSION = "allow_session"
    DENY = "deny"


PromptFn = Callable[[str, dict[str, Any]], Awaitable[ApprovalDecision]]


class ApprovalGate:
    def __init__(
        self,
        prompt_fn: PromptFn,
        *,
        store: SessionStore | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        always_allow: Iterable[str] = (),
        always_deny: Iterable[str] = (),
    ) -> None:
        self._prompt = prompt_fn
        self._store = store
        self._user_id = user_id
        self._session_id = session_id
        self._always_allow = set(always_allow)
        self._always_deny = set(always_deny)
        self._session_allow: set[str] = set()

    async def check(self, tool_name: str, args: dict[str, Any]) -> bool:
        if tool_name in self._always_deny:
            await self._record(tool_name, ApprovalDecision.DENY, "config")
            return False
        if tool_name in self._always_allow:
            await self._record(tool_name, ApprovalDecision.ALLOW_SESSION, "config")
            return True
        if tool_name in self._session_allow:
            return True

        decision = await self._prompt(tool_name, args)
        scope = "session" if decision == ApprovalDecision.ALLOW_SESSION else "once"
        await self._record(tool_name, decision, scope)

        if decision == ApprovalDecision.ALLOW_SESSION:
            self._session_allow.add(tool_name)
            return True
        return decision == ApprovalDecision.ALLOW_ONCE

    def reset_session(self) -> None:
        self._session_allow.clear()

    async def _record(self, tool_name: str, decision: ApprovalDecision, scope: str) -> None:
        if self._store is None or self._user_id is None:
            return
        await self._store.record_governance_decision(
            user_id=self._user_id,
            tool_name=tool_name,
            decision=decision.value,
            scope=scope,
            session_id=self._session_id,
        )


_CLI_HELP = (
    "[1] Allow Once   [2] Allow All This Session   [3] Deny  (default: 3)"
)


def _format_args(args: dict[str, Any], width: int = 200) -> str:
    text = ", ".join(f"{k}={v!r}" for k, v in args.items())
    return text if len(text) <= width else text[: width - 1] + "…"


def _read_choice(tool_name: str, args: dict[str, Any]) -> ApprovalDecision:
    print(f"\nTool request: {tool_name}({_format_args(args)})")
    print(_CLI_HELP)
    raw = input("> ").strip()
    if raw == "1":
        return ApprovalDecision.ALLOW_ONCE
    if raw == "2":
        return ApprovalDecision.ALLOW_SESSION
    return ApprovalDecision.DENY


async def cli_prompt(tool_name: str, args: dict[str, Any]) -> ApprovalDecision:
    """Default CLI prompt — synchronous `input()` in a worker thread so
    it doesn't block the event loop."""
    return await asyncio.to_thread(_read_choice, tool_name, args)


__all__ = ["ApprovalDecision", "ApprovalGate", "PromptFn", "cli_prompt"]
