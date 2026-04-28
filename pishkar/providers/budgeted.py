"""BudgetedProvider — wraps a `ModelProvider` with a per-user daily token cap.

Two behaviors layered on top of any inner provider:

* **Auto-concise at 70% utilization.** A short directive is appended to the
  system prompt nudging the model toward terse output. Threshold is
  configurable.
* **Hard stop at 100%.** Raises `BudgetExceeded` before contacting the
  inner provider.

Spend is read from `SessionStore.tokens_spent_since` over the current UTC
day, and freshly-observed `Usage` from each turn is written back via
`record_token_spend`. Without a `user_id` the wrapper is a pass-through.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from pishkar.providers.base import ModelProvider, ProviderChunk
from pishkar.workspace.store import SessionStore

CONCISE_DIRECTIVE = (
    "Token budget for today is running low. Be concise: short sentences, "
    "no preamble, only the essential answer."
)


class BudgetExceeded(RuntimeError):
    def __init__(self, user_id: str, spent: int, budget: int) -> None:
        super().__init__(f"Daily token budget exhausted for {user_id}: {spent}/{budget}")
        self.user_id = user_id
        self.spent = spent
        self.budget = budget


def _start_of_utc_day() -> str:
    now = datetime.now(UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


class BudgetedProvider(ModelProvider):
    def __init__(
        self,
        inner: ModelProvider,
        store: SessionStore,
        daily_budget_tokens: int,
        *,
        concise_threshold: float = 0.7,
        model_label: str = "unknown",
    ) -> None:
        self._inner = inner
        self._store = store
        self._budget = daily_budget_tokens
        self._threshold = concise_threshold
        self._model_label = model_label

    async def _spent_today(self, user_id: str) -> int:
        inp, out = await self._store.tokens_spent_since(user_id, _start_of_utc_day())
        return inp + out

    async def stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
        max_tokens: int | None = None,
        user_id: str | None = None,
    ) -> AsyncIterator[ProviderChunk]:
        effective_system = system

        if user_id is not None:
            spent = await self._spent_today(user_id)
            if spent >= self._budget:
                raise BudgetExceeded(user_id, spent, self._budget)
            if spent >= self._budget * self._threshold:
                effective_system = (
                    f"{system}\n\n{CONCISE_DIRECTIVE}" if system else CONCISE_DIRECTIVE
                )

        async for chunk in self._inner.stream(
            model=model,
            messages=messages,
            tools=tools,
            system=effective_system,
            max_tokens=max_tokens,
            user_id=user_id,
        ):
            if chunk.usage is not None and user_id is not None:
                await self._store.record_token_spend(
                    user_id=user_id,
                    model=model or self._model_label,
                    input_tokens=chunk.usage.input_tokens,
                    output_tokens=chunk.usage.output_tokens,
                )
            yield chunk


__all__ = ["BudgetExceeded", "BudgetedProvider", "CONCISE_DIRECTIVE"]
