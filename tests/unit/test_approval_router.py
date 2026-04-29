"""Unit tests for `ApprovalRouter`.

The router bridges the in-loop approval gate to the channel: it sends an
`ApprovalRequest` event over the bound channel, awaits a future keyed by
request id, and resolves the future when the channel reports a
decision.
"""

import asyncio

import pytest

from pishkar.core.events import ApprovalRequest, Event
from pishkar.gateway.approval_router import ApprovalRouter
from pishkar.tools.approval_gate import ApprovalDecision


class _Sink:
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def send(self, event: Event) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_request_resolves_when_channel_responds() -> None:
    router = ApprovalRouter(timeout_s=1.0)
    sink = _Sink()
    router.bind("s1", sink.send)

    async def respond_after_send() -> ApprovalDecision:
        return await router.request(
            session_id="s1", turn_id="t1", tool_name="bash", args={"cmd": "ls"}
        )

    task = asyncio.create_task(respond_after_send())
    # Yield until the request is registered and the event has been sent.
    for _ in range(20):
        await asyncio.sleep(0)
        if sink.events:
            break
    assert sink.events, "ApprovalRequest event should have been sent"
    sent = sink.events[0]
    assert isinstance(sent, ApprovalRequest)
    assert sent.tool_name == "bash"

    router.resolve("s1", sent.request_id, ApprovalDecision.ALLOW_ONCE)
    assert await task == ApprovalDecision.ALLOW_ONCE


@pytest.mark.asyncio
async def test_request_denies_when_no_channel_bound() -> None:
    router = ApprovalRouter(timeout_s=1.0)
    decision = await router.request(
        session_id="missing", turn_id="t1", tool_name="bash", args={}
    )
    assert decision == ApprovalDecision.DENY


@pytest.mark.asyncio
async def test_request_denies_on_timeout() -> None:
    router = ApprovalRouter(timeout_s=0.05)
    sink = _Sink()
    router.bind("s1", sink.send)
    decision = await router.request(
        session_id="s1", turn_id="t1", tool_name="bash", args={}
    )
    assert decision == ApprovalDecision.DENY


@pytest.mark.asyncio
async def test_unbind_denies_pending_requests() -> None:
    router = ApprovalRouter(timeout_s=5.0)
    sink = _Sink()
    router.bind("s1", sink.send)

    task = asyncio.create_task(
        router.request(session_id="s1", turn_id="t1", tool_name="bash", args={})
    )
    for _ in range(20):
        await asyncio.sleep(0)
        if sink.events:
            break

    router.unbind("s1")
    assert await task == ApprovalDecision.DENY


@pytest.mark.asyncio
async def test_resolve_for_unknown_request_is_noop() -> None:
    router = ApprovalRouter()
    # Should not raise.
    router.resolve("s1", "never-sent", ApprovalDecision.ALLOW_ONCE)
