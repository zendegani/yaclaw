"""Tests for the `plan` tool."""

from pathlib import Path

import pytest

from pishkar.core.context import current_session_id, current_user_id
from pishkar.tools import plan as plan_mod


async def test_plan_persists_when_context_set(monkeypatch: pytest.MonkeyPatch,
                                              tmp_path: Path) -> None:
    monkeypatch.setattr(plan_mod, "_BASE_DIR", tmp_path)
    current_user_id.set("ali")
    current_session_id.set("s1")

    body = "- [ ] step 1\n- [ ] step 2\n"
    out = await plan_mod.plan(body)

    expected = tmp_path / "users" / "ali" / "plans" / "s1.md"
    assert expected.exists()
    assert expected.read_text() == body
    assert "Plan saved to" in out
    assert body in out  # plan content is echoed back to history


async def test_plan_revision_overwrites(monkeypatch: pytest.MonkeyPatch,
                                        tmp_path: Path) -> None:
    monkeypatch.setattr(plan_mod, "_BASE_DIR", tmp_path)
    current_user_id.set("ali")
    current_session_id.set("s1")

    await plan_mod.plan("v1")
    await plan_mod.plan("v2")
    saved = tmp_path / "users" / "ali" / "plans" / "s1.md"
    assert saved.read_text() == "v2"


async def test_plan_outside_context_just_echoes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(plan_mod, "_BASE_DIR", tmp_path)
    current_user_id.set("")
    current_session_id.set("")
    out = await plan_mod.plan("standalone plan")
    assert out == "standalone plan"
    assert not (tmp_path / "users").exists()


def test_plan_is_registered_in_default_registry() -> None:
    from pishkar.runtime import _default_registry

    reg = _default_registry()
    assert "plan" in reg.names()


def test_default_system_nudges_planning() -> None:
    from pishkar.runtime import DEFAULT_SYSTEM

    assert "plan" in DEFAULT_SYSTEM.lower()
    assert "checklist" in DEFAULT_SYSTEM.lower() or "plan" in DEFAULT_SYSTEM
