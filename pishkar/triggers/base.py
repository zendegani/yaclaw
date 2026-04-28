"""Trigger Protocol — non-channel sources of `InboundMessage`s.

A trigger runs in the background and pushes synthetic messages into the
gateway when an external condition fires (heartbeat tick, webhook, MQTT
event, calendar entry, …). The shape mirrors `Channel` but inverted:
triggers don't have a user typing at them, they decide on their own
when to inject.

The `submit` callable is `Gateway.submit`; passing it in (rather than
giving the trigger a reference to the gateway) keeps triggers testable
and prevents them from reaching for state they don't own.
"""

from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from pishkar.core.messages import InboundMessage

Submit = Callable[[InboundMessage], Awaitable[None]]


@runtime_checkable
class TriggerSource(Protocol):
    name: str

    async def run(self, submit: Submit) -> None: ...

    async def stop(self) -> None: ...


__all__ = ["Submit", "TriggerSource"]
