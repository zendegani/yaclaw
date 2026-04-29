"""Per-server router that bridges in-loop approval prompts to a channel.

The agent loop runs the approval gate's `prompt_fn` from inside a tool
call. To reach the user, the prompt must travel out over the channel
(WebSocket) and a response must come back. `ApprovalRouter` owns the
correlation.

Flow per request:
1. Runtime wires `ApprovalRouter.request` as the gate's `prompt_fn`.
2. `request` allocates a `request_id`, registers a future, sends an
   `ApprovalRequest` event over the bound channel for the session, and
   awaits the future (with a timeout — disconnect or no-answer denies).
3. The WS handler on the server pulls non-message control frames out of
   the inbound stream and calls `resolve(session_id, request_id, decision)`.

If no channel is bound to the session (offline trigger, headless tool
run), `request` denies immediately rather than hanging.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from pishkar.core.events import ApprovalRequest, Event
from pishkar.tools.approval_gate import ApprovalDecision

log = logging.getLogger(__name__)

ChannelSend = Callable[[Event], Awaitable[None]]

DEFAULT_APPROVAL_TIMEOUT_S = 120.0


class ApprovalRouter:
    def __init__(self, *, timeout_s: float = DEFAULT_APPROVAL_TIMEOUT_S) -> None:
        self._channels: dict[str, ChannelSend] = {}
        # (session_id, request_id) -> Future[ApprovalDecision]
        self._pending: dict[tuple[str, str], asyncio.Future[ApprovalDecision]] = {}
        self._timeout_s = timeout_s

    def bind(self, session_id: str, send: ChannelSend) -> None:
        self._channels[session_id] = send

    def unbind(self, session_id: str) -> None:
        self._channels.pop(session_id, None)
        # Fail any pending requests for this session — the user is gone.
        for key in [k for k in self._pending if k[0] == session_id]:
            fut = self._pending.pop(key)
            if not fut.done():
                fut.set_result(ApprovalDecision.DENY)

    def resolve(self, session_id: str, request_id: str, decision: ApprovalDecision) -> None:
        fut = self._pending.pop((session_id, request_id), None)
        if fut is not None and not fut.done():
            fut.set_result(decision)

    async def request(
        self,
        *,
        session_id: str,
        turn_id: str,
        tool_name: str,
        args: dict[str, Any],
    ) -> ApprovalDecision:
        send = self._channels.get(session_id)
        if send is None:
            log.warning(
                "approval_router: no channel bound for session %r — denying %s; "
                "bound sessions: %s",
                session_id,
                tool_name,
                list(self._channels),
            )
            return ApprovalDecision.DENY

        request_id = str(uuid4())
        log.info(
            "approval_router: prompting user for %s (session=%s, request_id=%s)",
            tool_name,
            session_id,
            request_id,
        )
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[ApprovalDecision] = loop.create_future()
        self._pending[(session_id, request_id)] = fut

        try:
            await send(
                ApprovalRequest(
                    turn_id=turn_id,
                    session_id=session_id,
                    request_id=request_id,
                    tool_name=tool_name,
                    input=args,
                )
            )
            return await asyncio.wait_for(fut, timeout=self._timeout_s)
        except (TimeoutError, asyncio.CancelledError):
            return ApprovalDecision.DENY
        finally:
            self._pending.pop((session_id, request_id), None)


__all__ = ["ApprovalRouter", "DEFAULT_APPROVAL_TIMEOUT_S"]
