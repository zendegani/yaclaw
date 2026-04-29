"""Full-stack: WebSocket -> Gateway -> run_turn -> tool dispatch -> stream back.

Uses a `MockLiteLLMProvider` (the canonical test seam called out in the
design) wrapping the real `LiteLLMProvider` parser via canned chunks. Hits
no network. Exercises the production composition from `pishkar.runtime`."""

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pishkar.providers.base import ModelProvider, ProviderChunk, ToolCallDelta, Usage
from pishkar.runtime import build_handler
from pishkar.server import create_app
from pishkar.tools.fs import read_file, write_file
from pishkar.tools.registry import ToolRegistry
from pishkar.tools.runner import SubprocessToolRunner
from pishkar.workspace.store import SessionStore


class MockLiteLLMProvider(ModelProvider):
    """Scripted provider — one list of chunks per LLM call (= per turn pass)."""

    def __init__(self, scripts: list[list[ProviderChunk]]) -> None:
        self._scripts = scripts
        self.calls = 0
        self.observed_messages: list[list[dict[str, Any]]] = []
        self.observed_systems: list[str | None] = []

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
        self.observed_messages.append([dict(m) for m in messages])
        self.observed_systems.append(system)
        chunks = self._scripts[self.calls]
        self.calls += 1
        for c in chunks:
            yield c


def _drain_until(ws, stop_type: str, limit: int = 80) -> list[dict]:
    received: list[dict] = []
    for _ in range(limit):
        text = ws.receive_text()
        payload = json.loads(text)
        received.append(payload)
        if payload.get("type") == stop_type:
            return received
    raise AssertionError(f"never saw {stop_type}; got {received}")


def _make_app(provider: ModelProvider, tmp_path: Path, *, registry: ToolRegistry | None = None):
    store = SessionStore(tmp_path / "sessions.db")
    runner = SubprocessToolRunner(registry, default_timeout_s=2.0) if registry else None
    handler = build_handler(
        provider=provider,
        model="claude-opus-4-7",
        registry=registry,
        runner=runner,
        system="test system",
    )
    return create_app(store=store, handler=handler)


def test_text_only_turn_streams_through_websocket(tmp_path: Path) -> None:
    provider = MockLiteLLMProvider([[
        ProviderChunk(text="Hello "),
        ProviderChunk(text="world"),
        ProviderChunk(stop_reason="stop"),
        ProviderChunk(usage=Usage(input_tokens=4, output_tokens=2)),
    ]])
    app = _make_app(provider, tmp_path)
    with TestClient(app) as client, client.websocket_connect("/ws/ali/s1") as ws:
        ws.send_text(json.dumps({"content": "hi"}))
        events = _drain_until(ws, "turn_end")

    text = "".join(
        e["delta"]["text"]
        for e in events
        if e["type"] == "content_block_delta" and e["delta"].get("type") == "text_delta"
    )
    assert text == "Hello world"
    assert events[-1]["stop_reason"] == "end_turn"
    assert provider.observed_messages[0][-1] == {"role": "user", "content": "hi"}


def test_tool_call_round_trip(tmp_path: Path) -> None:
    """LLM asks to write a file, runner executes, follow-up turn returns text."""
    target = tmp_path / "note.txt"
    tc = ToolCallDelta(
        index=0,
        id="call_1",
        name="write_file",
        arguments=json.dumps({"path": str(target), "content": "from-pishkar"}),
    )
    provider = MockLiteLLMProvider([
        [ProviderChunk(tool_calls=[tc]), ProviderChunk(stop_reason="tool_calls")],
        [ProviderChunk(text="done"), ProviderChunk(stop_reason="stop")],
    ])
    reg = ToolRegistry()
    reg.register_many(read_file, write_file)
    app = _make_app(provider, tmp_path, registry=reg)

    with TestClient(app) as client, client.websocket_connect("/ws/ali/s1") as ws:
        ws.send_text(json.dumps({"content": "save a note"}))
        events = _drain_until(ws, "turn_end")

    assert target.read_text() == "from-pishkar"
    types = [e["type"] for e in events]
    assert "tool_result" in types
    assert any(
        e["type"] == "content_block_start" and e["content_block"]["type"] == "tool_use"
        for e in events
    )
    final_text = "".join(
        e["delta"]["text"]
        for e in events
        if e["type"] == "content_block_delta" and e["delta"].get("type") == "text_delta"
    )
    assert final_text == "done"


def test_history_persists_across_turns_in_same_session(tmp_path: Path) -> None:
    provider = MockLiteLLMProvider([
        [ProviderChunk(text="one"), ProviderChunk(stop_reason="stop")],
        [ProviderChunk(text="two"), ProviderChunk(stop_reason="stop")],
    ])
    app = _make_app(provider, tmp_path)
    with TestClient(app) as client, client.websocket_connect("/ws/ali/s1") as ws:
        ws.send_text(json.dumps({"content": "first"}))
        _drain_until(ws, "turn_end")
        ws.send_text(json.dumps({"content": "second"}))
        _drain_until(ws, "turn_end")

    # Second LLM call must see the first user+assistant exchange in history.
    second_msgs = provider.observed_messages[1]
    contents = [m.get("content") for m in second_msgs]
    assert "first" in contents
    assert "second" in contents


def test_replay_after_reconnect_includes_tool_events(tmp_path: Path) -> None:
    target = tmp_path / "x.txt"
    tc = ToolCallDelta(
        index=0,
        id="c1",
        name="write_file",
        arguments=json.dumps({"path": str(target), "content": "v1"}),
    )
    provider = MockLiteLLMProvider([
        [ProviderChunk(tool_calls=[tc]), ProviderChunk(stop_reason="tool_calls")],
        [ProviderChunk(text="ok"), ProviderChunk(stop_reason="stop")],
    ])
    reg = ToolRegistry()
    reg.register_many(read_file, write_file)
    app = _make_app(provider, tmp_path, registry=reg)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/ali/s1") as ws:
            ws.send_text(json.dumps({"content": "go"}))
            _drain_until(ws, "turn_end")

        # Reconnect with no last_event_id → full replay.
        with client.websocket_connect("/ws/ali/s1") as ws:
            replayed = _drain_until(ws, "turn_end")

    types = [e["type"] for e in replayed]
    assert "tool_result" in types
    assert types[-1] == "turn_end"


def test_workspace_files_appear_in_system_prompt(tmp_path: Path) -> None:
    from pishkar.runtime import build_handler
    from pishkar.workspace.loader import WorkspaceLoader

    loader = WorkspaceLoader(base_dir=tmp_path)
    loader.ensure_starter("ali")
    (tmp_path / "users" / "ali" / "USER.md").write_text("User lives in Tehran.")

    provider = MockLiteLLMProvider([[
        ProviderChunk(text="ok"), ProviderChunk(stop_reason="stop"),
    ]])
    store = SessionStore(tmp_path / "sessions.db")
    handler = build_handler(
        provider=provider,
        model="m",
        system="BASE SYSTEM",
        workspace_loader=loader,
    )
    app = create_app(store=store, handler=handler)
    with TestClient(app) as client, client.websocket_connect("/ws/ali/s1") as ws:
        ws.send_text(json.dumps({"content": "hi"}))
        _drain_until(ws, "turn_end")

    blob = provider.observed_systems[0] or ""
    assert "BASE SYSTEM" in blob
    assert "Pishkar" in blob  # from SOUL.md
    assert "Tehran" in blob   # from USER.md


@pytest.mark.parametrize("session_a,session_b", [("s1", "s2")])
def test_sessions_run_concurrently_with_independent_history(
    tmp_path: Path, session_a: str, session_b: str
) -> None:
    provider = MockLiteLLMProvider([
        [ProviderChunk(text="A"), ProviderChunk(stop_reason="stop")],
        [ProviderChunk(text="B"), ProviderChunk(stop_reason="stop")],
    ])
    app = _make_app(provider, tmp_path)
    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/ali/{session_a}") as a:
            a.send_text(json.dumps({"content": "for-a"}))
            ev_a = _drain_until(a, "turn_end")
        with client.websocket_connect(f"/ws/ali/{session_b}") as b:
            b.send_text(json.dumps({"content": "for-b"}))
            ev_b = _drain_until(b, "turn_end")

    assert all("for-b" not in json.dumps(e) for e in ev_a)
    assert all("for-a" not in json.dumps(e) for e in ev_b)
