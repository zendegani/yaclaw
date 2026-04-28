from pathlib import Path

import pytest

from pishkar.workspace.atomic_io import atomic_write_text


def test_atomic_write_creates_file(tmp_path: Path) -> None:
    target = tmp_path / "soul.md"
    atomic_write_text(target, "hello")
    assert target.read_text() == "hello"


def test_atomic_write_overwrites(tmp_path: Path) -> None:
    target = tmp_path / "user.md"
    target.write_text("original")
    atomic_write_text(target, "replaced")
    assert target.read_text() == "replaced"


def test_atomic_write_creates_parent_dirs(tmp_path: Path) -> None:
    target = tmp_path / "users" / "ali" / "USER.md"
    atomic_write_text(target, "Name: Ali")
    assert target.read_text() == "Name: Ali"


def test_atomic_write_leaves_no_temp_files(tmp_path: Path) -> None:
    target = tmp_path / "heartbeat.md"
    atomic_write_text(target, "tasks")
    leftovers = [p for p in tmp_path.iterdir() if p != target]
    assert leftovers == []


def test_atomic_write_preserves_original_on_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pishkar.workspace.atomic_io as mod

    target = tmp_path / "soul.md"
    target.write_text("original")

    def boom(src: str, dst: str) -> None:
        raise RuntimeError("simulated rename failure")

    monkeypatch.setattr(mod.os, "replace", boom)
    with pytest.raises(RuntimeError):
        atomic_write_text(target, "new content")

    assert target.read_text() == "original"
    leftovers = [p for p in tmp_path.iterdir() if p != target]
    assert leftovers == []
