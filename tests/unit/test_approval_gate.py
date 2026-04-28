from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from pishkar.tools.approval_gate import (
    ApprovalDecision,
    ApprovalGate,
    cli_prompt,
)
from pishkar.workspace.store import SessionStore


@pytest.fixture
async def store(tmp_path: Path):
    s = SessionStore(tmp_path / "sessions.db")
    await s.open()
    try:
        yield s
    finally:
        await s.close()


def _scripted_prompt(*answers: ApprovalDecision):
    seq = list(answers)
    calls: list[tuple[str, dict[str, Any]]] = []

    async def prompt(name: str, args: dict[str, Any]) -> ApprovalDecision:
        calls.append((name, args))
        return seq.pop(0)

    return prompt, calls


async def test_always_allow_skips_prompt() -> None:
    prompt, calls = _scripted_prompt()
    gate = ApprovalGate(prompt, always_allow={"safe"})
    assert await gate.check("safe", {}) is True
    assert calls == []


async def test_always_deny_skips_prompt() -> None:
    prompt, calls = _scripted_prompt()
    gate = ApprovalGate(prompt, always_deny={"dangerous"})
    assert await gate.check("dangerous", {}) is False
    assert calls == []


async def test_allow_once_runs_then_asks_again() -> None:
    prompt, calls = _scripted_prompt(
        ApprovalDecision.ALLOW_ONCE, ApprovalDecision.DENY
    )
    gate = ApprovalGate(prompt)
    assert await gate.check("bash", {"cmd": "ls"}) is True
    assert await gate.check("bash", {"cmd": "ls"}) is False
    assert len(calls) == 2


async def test_allow_session_remembers_until_reset() -> None:
    prompt, calls = _scripted_prompt(ApprovalDecision.ALLOW_SESSION)
    gate = ApprovalGate(prompt)
    assert await gate.check("bash", {"cmd": "ls"}) is True
    assert await gate.check("bash", {"cmd": "rm"}) is True  # no re-prompt
    assert len(calls) == 1

    gate.reset_session()
    prompt2, calls2 = _scripted_prompt(ApprovalDecision.DENY)
    gate._prompt = prompt2  # type: ignore[assignment]
    assert await gate.check("bash", {"cmd": "ls"}) is False
    assert len(calls2) == 1


async def test_deny_returns_false() -> None:
    prompt, _ = _scripted_prompt(ApprovalDecision.DENY)
    gate = ApprovalGate(prompt)
    assert await gate.check("bash", {}) is False


async def test_records_governance_decision(store: SessionStore) -> None:
    prompt, _ = _scripted_prompt(ApprovalDecision.ALLOW_SESSION)
    gate = ApprovalGate(prompt, store=store, user_id="ali", session_id="s1")
    await gate.check("bash", {"cmd": "ls"})

    async with store.db.execute(
        "SELECT user_id, session_id, tool_name, decision, scope FROM governance_decisions"
    ) as cur:
        rows = await cur.fetchall()
    assert rows == [("ali", "s1", "bash", "allow_session", "session")]


async def test_records_config_scope_for_always_allow(store: SessionStore) -> None:
    prompt, _ = _scripted_prompt()
    gate = ApprovalGate(
        prompt, store=store, user_id="ali", always_allow={"read_file"}
    )
    await gate.check("read_file", {"path": "/tmp/x"})
    async with store.db.execute(
        "SELECT decision, scope FROM governance_decisions"
    ) as cur:
        assert await cur.fetchall() == [("allow_session", "config")]


async def test_no_recording_without_store_or_user(store: SessionStore) -> None:
    prompt, _ = _scripted_prompt(ApprovalDecision.ALLOW_ONCE)
    gate = ApprovalGate(prompt, store=store)  # no user_id
    await gate.check("bash", {})
    async with store.db.execute("SELECT COUNT(*) FROM governance_decisions") as cur:
        assert (await cur.fetchone())[0] == 0


async def test_gate_pluggable_into_runner() -> None:
    from pishkar.tools.registry import ToolRegistry, tool
    from pishkar.tools.runner import SubprocessToolRunner

    @tool()
    async def ping() -> str:
        return "pong"

    reg = ToolRegistry()
    reg.register(ping)

    prompt, _ = _scripted_prompt(ApprovalDecision.ALLOW_ONCE)
    gate = ApprovalGate(prompt)
    runner = SubprocessToolRunner(reg, approval_fn=gate.check)

    result = await runner.run("ping", {})
    assert result.content == "pong" and not result.denied


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1", ApprovalDecision.ALLOW_ONCE),
        ("2", ApprovalDecision.ALLOW_SESSION),
        ("3", ApprovalDecision.DENY),
        ("", ApprovalDecision.DENY),
        ("garbage", ApprovalDecision.DENY),
    ],
)
async def test_cli_prompt_parses_choices(raw: str, expected: ApprovalDecision) -> None:
    with patch("builtins.input", return_value=raw), patch("builtins.print"):
        assert await cli_prompt("bash", {"cmd": "ls"}) == expected
