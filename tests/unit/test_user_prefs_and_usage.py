"""Tests for user_prefs persistence + per-model spend rollups."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pishkar.channels.telegram import _format_usage_section
from pishkar.workspace.store import SessionStore


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[SessionStore]:
    s = SessionStore(tmp_path / "p.db")
    await s.open()
    try:
        yield s
    finally:
        await s.close()


# --- user_prefs --------------------------------------------------------------


async def test_get_pref_returns_none_when_missing(store: SessionStore) -> None:
    assert await store.get_pref("ali", "model") is None


async def test_set_pref_then_get_pref_round_trips(store: SessionStore) -> None:
    await store.set_pref("ali", "model", "groq/llama-3.3-70b-versatile")
    assert await store.get_pref("ali", "model") == "groq/llama-3.3-70b-versatile"


async def test_set_pref_overwrites_existing(store: SessionStore) -> None:
    await store.set_pref("ali", "model", "v1")
    await store.set_pref("ali", "model", "v2")
    assert await store.get_pref("ali", "model") == "v2"


async def test_prefs_are_per_user(store: SessionStore) -> None:
    await store.set_pref("ali", "model", "anthropic/m1")
    await store.set_pref("guest", "model", "groq/m2")
    assert await store.get_pref("ali", "model") == "anthropic/m1"
    assert await store.get_pref("guest", "model") == "groq/m2"


# --- tokens_spent_by_model_since --------------------------------------------


async def test_per_model_rollup_groups_and_orders_by_total(
    store: SessionStore,
) -> None:
    await store.record_token_spend("ali", "groq/scout", 100, 50)
    await store.record_token_spend("ali", "groq/scout", 200, 100)
    await store.record_token_spend("ali", "claude-opus", 10, 5)

    rows = await store.tokens_spent_by_model_since(
        "ali", datetime.fromtimestamp(0, tz=UTC).isoformat()
    )
    assert rows[0] == ("groq/scout", 300, 150)  # higher total comes first
    assert rows[1] == ("claude-opus", 10, 5)


async def test_per_model_rollup_respects_since(store: SessionStore) -> None:
    await store.record_token_spend("ali", "m", 100, 100)
    rows = await store.tokens_spent_by_model_since(
        "ali", (datetime.now(UTC) + timedelta(days=1)).isoformat()
    )
    assert rows == []


async def test_per_model_rollup_is_per_user(store: SessionStore) -> None:
    await store.record_token_spend("ali", "m", 100, 100)
    await store.record_token_spend("guest", "m", 5, 5)
    rows = await store.tokens_spent_by_model_since(
        "guest", datetime.fromtimestamp(0, tz=UTC).isoformat()
    )
    assert rows == [("m", 5, 5)]


# --- _format_usage_section ---------------------------------------------------


def test_format_usage_section_handles_empty() -> None:
    out = _format_usage_section("Today", [])
    assert "Today" in out
    assert "nothing yet" in out


def test_format_usage_section_renders_rows_and_total() -> None:
    rows = [("groq/scout", 300, 150), ("claude-opus", 10, 5)]
    out = _format_usage_section("Last 7 days", rows)
    assert "<b>Last 7 days</b>" in out
    assert "groq/scout" in out
    assert "300" in out and "150" in out
    assert "Total:" in out
    # Comma-formatted thousands separators.
    assert "310" in out  # 300 + 10
