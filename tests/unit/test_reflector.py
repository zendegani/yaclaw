"""Tests for the Reflector — periodic MEMORY.md extraction."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from pishkar.gateway.hooks import ON_TURN_COMPLETE, HookManager
from pishkar.observability.reflector import Reflector
from pishkar.providers.base import ProviderChunk
from pishkar.workspace.loader import WorkspaceLoader
from pishkar.workspace.store import SessionStore


class _FakeProvider:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def stream(self, **kwargs: Any) -> AsyncIterator[ProviderChunk]:
        self.calls.append(kwargs)
        yield ProviderChunk(text=self.response)


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[SessionStore]:
    s = SessionStore(tmp_path / "s.db")
    await s.open()
    try:
        yield s
    finally:
        await s.close()


def _loader(tmp_path: Path) -> WorkspaceLoader:
    return WorkspaceLoader(base_dir=tmp_path)


async def _seed_history(store: SessionStore, session_id: str, n: int = 4) -> None:
    from pishkar.core.messages import InboundMessage, OutboundMessage

    for i in range(n):
        await store.enqueue_inbound(
            InboundMessage(
                user_id="ali",
                session_id=session_id,
                channel="ws",
                content=f"user msg {i}",
            )
        )
        await store.record_outbound(
            OutboundMessage(
                user_id="ali",
                session_id=session_id,
                channel="ws",
                content=f"assistant msg {i}",
            )
        )


async def test_reflector_does_not_run_until_threshold(
    tmp_path: Path, store: SessionStore
) -> None:
    provider = _FakeProvider(response="updated memory body")
    loader = _loader(tmp_path)
    refl = Reflector(
        provider=provider,
        model="m",
        store=store,
        loader=loader,
        every_n_turns=3,
    )
    hm = HookManager()
    refl.attach(hm)
    await _seed_history(store, "s1", n=2)

    for _ in range(2):
        hm.emit(
            ON_TURN_COMPLETE,
            turn_id="t", session_id="s1", user_id="ali", stop_reason="end_turn",
        )
        await hm.drain()

    assert provider.calls == []
    assert loader.load("ali").memory == ""


async def test_reflector_runs_at_threshold_and_writes_memory(
    tmp_path: Path, store: SessionStore
) -> None:
    provider = _FakeProvider(response="- User prefers concise answers.\n")
    loader = _loader(tmp_path)
    refl = Reflector(
        provider=provider, model="m", store=store, loader=loader, every_n_turns=3,
    )
    hm = HookManager()
    refl.attach(hm)
    await _seed_history(store, "s1", n=4)

    for _ in range(3):
        hm.emit(
            ON_TURN_COMPLETE,
            turn_id="t", session_id="s1", user_id="ali", stop_reason="end_turn",
        )
        await hm.drain()

    assert len(provider.calls) == 1
    ws = loader.load("ali")
    assert "User prefers concise answers." in ws.memory


async def test_reflector_skips_errored_turns(
    tmp_path: Path, store: SessionStore
) -> None:
    provider = _FakeProvider(response="should not run")
    loader = _loader(tmp_path)
    refl = Reflector(
        provider=provider, model="m", store=store, loader=loader, every_n_turns=2,
    )
    hm = HookManager()
    refl.attach(hm)
    await _seed_history(store, "s1", n=2)

    for _ in range(5):
        hm.emit(
            ON_TURN_COMPLETE,
            turn_id="t", session_id="s1", user_id="ali", stop_reason="error",
        )
        await hm.drain()

    assert provider.calls == []


async def test_reflector_passes_existing_memory_in_prompt(
    tmp_path: Path, store: SessionStore
) -> None:
    provider = _FakeProvider(response="merged memory")
    loader = _loader(tmp_path)
    loader.write("ali", "MEMORY", "- User is Ali.\n")
    refl = Reflector(
        provider=provider, model="m", store=store, loader=loader, every_n_turns=1,
    )
    hm = HookManager()
    refl.attach(hm)
    await _seed_history(store, "s1", n=1)

    hm.emit(
        ON_TURN_COMPLETE,
        turn_id="t", session_id="s1", user_id="ali", stop_reason="end_turn",
    )
    await hm.drain()

    [call] = provider.calls
    [user_msg] = call["messages"]
    assert "- User is Ali." in user_msg["content"]


async def test_reflector_swallows_provider_failure(
    tmp_path: Path, store: SessionStore
) -> None:
    class _Boom:
        async def stream(self, **_: Any) -> AsyncIterator[ProviderChunk]:
            raise RuntimeError("model down")
            yield  # pragma: no cover

    loader = _loader(tmp_path)
    loader.write("ali", "MEMORY", "- pre-existing\n")
    refl = Reflector(
        provider=_Boom(), model="m", store=store, loader=loader, every_n_turns=1,
    )
    hm = HookManager()
    refl.attach(hm)
    await _seed_history(store, "s1", n=1)

    hm.emit(
        ON_TURN_COMPLETE,
        turn_id="t", session_id="s1", user_id="ali", stop_reason="end_turn",
    )
    await hm.drain()

    # Pre-existing memory untouched.
    assert loader.load("ali").memory == "- pre-existing\n"
