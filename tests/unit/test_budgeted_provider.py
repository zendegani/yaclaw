from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from pishkar.providers.base import ModelProvider, ProviderChunk, Usage
from pishkar.providers.budgeted import (
    CONCISE_DIRECTIVE,
    BudgetedProvider,
    BudgetExceeded,
)
from pishkar.workspace.store import SessionStore


class FakeProvider(ModelProvider):
    def __init__(self, chunks: list[ProviderChunk]) -> None:
        self._chunks = chunks
        self.calls: list[dict[str, Any]] = []

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
        self.calls.append({"model": model, "system": system, "user_id": user_id})
        for c in self._chunks:
            yield c


@pytest.fixture
async def store(tmp_path: Path):
    s = SessionStore(tmp_path / "sessions.db")
    await s.open()
    try:
        yield s
    finally:
        await s.close()


async def _drain(provider: BudgetedProvider, **kwargs: Any) -> list[ProviderChunk]:
    return [c async for c in provider.stream(**kwargs)]


async def test_under_threshold_passes_system_through(store: SessionStore) -> None:
    inner = FakeProvider([ProviderChunk(text="ok")])
    bp = BudgetedProvider(inner, store, daily_budget_tokens=1000)

    await _drain(bp, model="m", messages=[{"role": "user", "content": "hi"}],
                 system="be helpful", user_id="ali")

    assert inner.calls[0]["system"] == "be helpful"


async def test_over_threshold_appends_concise_directive(store: SessionStore) -> None:
    await store.record_token_spend("ali", "m", 600, 200)  # 800 / 1000 = 80%
    inner = FakeProvider([ProviderChunk(text="ok")])
    bp = BudgetedProvider(inner, store, daily_budget_tokens=1000)

    await _drain(bp, model="m", messages=[{"role": "user", "content": "hi"}],
                 system="be helpful", user_id="ali")

    seen = inner.calls[0]["system"]
    assert "be helpful" in seen
    assert CONCISE_DIRECTIVE in seen


async def test_over_threshold_with_no_system_uses_directive_alone(store: SessionStore) -> None:
    await store.record_token_spend("ali", "m", 800, 0)
    inner = FakeProvider([ProviderChunk(text="ok")])
    bp = BudgetedProvider(inner, store, daily_budget_tokens=1000)

    await _drain(bp, model="m", messages=[{"role": "user", "content": "hi"}], user_id="ali")
    assert inner.calls[0]["system"] == CONCISE_DIRECTIVE


async def test_at_or_over_budget_raises(store: SessionStore) -> None:
    await store.record_token_spend("ali", "m", 1000, 0)
    inner = FakeProvider([ProviderChunk(text="should not appear")])
    bp = BudgetedProvider(inner, store, daily_budget_tokens=1000)

    with pytest.raises(BudgetExceeded) as exc:
        await _drain(bp, model="m", messages=[{"role": "user", "content": "hi"}], user_id="ali")
    assert exc.value.spent == 1000
    assert inner.calls == []


async def test_usage_chunks_record_spend(store: SessionStore) -> None:
    inner = FakeProvider([
        ProviderChunk(text="hi"),
        ProviderChunk(usage=Usage(input_tokens=12, output_tokens=8)),
    ])
    bp = BudgetedProvider(inner, store, daily_budget_tokens=10_000)

    await _drain(bp, model="claude-opus", messages=[{"role": "user", "content": "x"}],
                 user_id="ali")

    inp, out = await store.tokens_spent_since("ali", "1970-01-01T00:00:00+00:00")
    assert (inp, out) == (12, 8)


async def test_no_user_id_skips_budget_and_recording(store: SessionStore) -> None:
    inner = FakeProvider([
        ProviderChunk(text="hi"),
        ProviderChunk(usage=Usage(input_tokens=5, output_tokens=5)),
    ])
    bp = BudgetedProvider(inner, store, daily_budget_tokens=1)

    chunks = await _drain(bp, model="m", messages=[{"role": "user", "content": "x"}])
    assert len(chunks) == 2

    inp, out = await store.tokens_spent_since("ali", "1970-01-01T00:00:00+00:00")
    assert (inp, out) == (0, 0)


async def _drain_and_settle(bp: BudgetedProvider, **kwargs: Any) -> None:
    """Drain the stream, then let fire-and-forget alert tasks run."""
    import asyncio

    await _drain(bp, **kwargs)
    await asyncio.gather(*bp._alert_tasks, return_exceptions=True)


async def test_alert_fires_once_when_threshold_crossed(store: SessionStore) -> None:
    await store.record_token_spend("ali", "m", 800, 0)  # 80% of 1000
    alerts: list[tuple[str, int, int, float]] = []

    async def on_alert(user_id: str, spent: int, budget: int, threshold: float) -> None:
        alerts.append((user_id, spent, budget, threshold))

    inner = FakeProvider([ProviderChunk(text="ok")])
    bp = BudgetedProvider(inner, store, daily_budget_tokens=1000, alert_fn=on_alert)

    await _drain_and_settle(bp, model="m", messages=[], user_id="ali")
    await _drain_and_settle(bp, model="m", messages=[], user_id="ali")

    assert alerts == [("ali", 800, 1000, 0.8)]


async def test_no_alert_below_threshold(store: SessionStore) -> None:
    await store.record_token_spend("ali", "m", 500, 0)
    alerts: list[float] = []

    async def on_alert(user_id: str, spent: int, budget: int, threshold: float) -> None:
        alerts.append(threshold)

    inner = FakeProvider([ProviderChunk(text="ok")])
    bp = BudgetedProvider(inner, store, daily_budget_tokens=1000, alert_fn=on_alert)

    await _drain_and_settle(bp, model="m", messages=[], user_id="ali")
    assert alerts == []


async def test_only_highest_crossed_threshold_fires(store: SessionStore) -> None:
    await store.record_token_spend("ali", "m", 900, 0)  # crosses 0.5 and 0.8
    alerts: list[float] = []

    async def on_alert(user_id: str, spent: int, budget: int, threshold: float) -> None:
        alerts.append(threshold)

    inner = FakeProvider([ProviderChunk(text="ok")])
    bp = BudgetedProvider(
        inner, store, daily_budget_tokens=1000,
        alert_thresholds=(0.5, 0.8), alert_fn=on_alert,
    )

    await _drain_and_settle(bp, model="m", messages=[], user_id="ali")
    assert alerts == [0.8]


async def test_alert_failure_does_not_break_stream(store: SessionStore) -> None:
    await store.record_token_spend("ali", "m", 800, 0)

    async def on_alert(user_id: str, spent: int, budget: int, threshold: float) -> None:
        raise RuntimeError("exporter down")

    inner = FakeProvider([ProviderChunk(text="ok")])
    bp = BudgetedProvider(inner, store, daily_budget_tokens=1000, alert_fn=on_alert)

    chunks = await _drain(bp, model="m", messages=[], user_id="ali")
    assert [c.text for c in chunks] == ["ok"]
    import asyncio

    await asyncio.gather(*bp._alert_tasks, return_exceptions=True)


async def test_alerts_tracked_per_user(store: SessionStore) -> None:
    await store.record_token_spend("ali", "m", 800, 0)
    await store.record_token_spend("bob", "m", 800, 0)
    alerts: list[str] = []

    async def on_alert(user_id: str, spent: int, budget: int, threshold: float) -> None:
        alerts.append(user_id)

    inner = FakeProvider([ProviderChunk(text="ok")])
    bp = BudgetedProvider(inner, store, daily_budget_tokens=1000, alert_fn=on_alert)

    await _drain_and_settle(bp, model="m", messages=[], user_id="ali")
    await _drain_and_settle(bp, model="m", messages=[], user_id="bob")
    assert sorted(alerts) == ["ali", "bob"]


async def test_yesterdays_spend_does_not_count(store: SessionStore) -> None:
    await store.db.execute(
        "INSERT INTO token_spend(user_id, model, input_tokens, output_tokens, "
        "cost_usd, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("ali", "m", 5000, 5000, None, "2000-01-01T00:00:00+00:00"),
    )
    await store.db.commit()

    inner = FakeProvider([ProviderChunk(text="ok")])
    bp = BudgetedProvider(inner, store, daily_budget_tokens=1000)
    await _drain(bp, model="m", messages=[{"role": "user", "content": "hi"}],
                 system="s", user_id="ali")
    assert inner.calls[0]["system"] == "s"
