"""Always-on append-only audit sink backed by `SessionStore`.

Two write paths:

* `write_event(event)` is called by the gateway/server for every yielded
  agent event. The row goes into the `events` table; the WebSocket
  reconnect path replays from here using `last_event_id`.
* `attach(hooks)` wires hook handlers that fold into the existing
  structured tables — `after_llm` → `token_spend`.

Both writes go through the same `SessionStore` connection. Failures
inside hook handlers are swallowed by `HookManager` (fail-open
contract); failures inside `write_event` propagate so the gateway can
decide whether to retry or close the channel.
"""

from typing import Any

from pishkar.core.events import Event
from pishkar.gateway.hooks import AFTER_LLM, HookManager
from pishkar.workspace.store import SessionStore


class SqliteSink:
    def __init__(self, store: SessionStore) -> None:
        self._store = store

    async def write_event(self, event: Event) -> None:
        await self._store.append_event(
            event_id=event.event_id,
            type=event.type,
            payload_json=event.model_dump_json(),
            turn_id=getattr(event, "turn_id", None),
            session_id=getattr(event, "session_id", None),
        )

    def attach(self, hooks: HookManager) -> None:
        hooks.on(AFTER_LLM, self._on_after_llm)

    async def _on_after_llm(
        self,
        *,
        user_id: str | None = None,
        model: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        **_: Any,
    ) -> None:
        if not user_id or not (input_tokens or output_tokens):
            return
        await self._store.record_token_spend(
            user_id=user_id,
            model=model or "unknown",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )


__all__ = ["SqliteSink"]
