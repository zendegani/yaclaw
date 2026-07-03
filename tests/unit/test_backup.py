import sqlite3
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

import pishkar.tools.backup as backup_tool
from pishkar.workspace.backup import (
    BackupBackend,
    LocalDirBackend,
    create_backup_archive,
)


def _make_state(base: Path) -> None:
    """A miniature ~/.pishkar with a real SQLite db and workspace files."""
    base.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(base / "sessions.db")
    conn.execute("CREATE TABLE t (v TEXT)")
    conn.execute("INSERT INTO t VALUES ('keep-me')")
    conn.commit()
    conn.close()
    (base / "users" / "ali").mkdir(parents=True)
    (base / "users" / "ali" / "SOUL.md").write_text("soul", encoding="utf-8")
    (base / "webhooks.json").write_text("[]", encoding="utf-8")
    # Regenerable assets that must stay out of the archive.
    (base / "piper").mkdir()
    (base / "piper" / "voice.onnx").write_bytes(b"\x00" * 128)
    (base / "logs").mkdir()
    (base / "logs" / "server.log").write_text("noise", encoding="utf-8")


def test_archive_contains_state_and_excludes_regenerables(tmp_path: Path) -> None:
    base, staging = tmp_path / "pishkar", tmp_path / "staging"
    _make_state(base)
    staging.mkdir()

    archive = create_backup_archive(base, staging)

    with tarfile.open(archive) as tar:
        names = tar.getnames()
    assert "sessions.db" in names
    assert "users/ali/SOUL.md" in names
    assert "webhooks.json" in names
    assert not any(n.startswith("piper") for n in names)
    assert not any(n.startswith("logs") for n in names)


def test_archive_name_is_timestamped(tmp_path: Path) -> None:
    base, staging = tmp_path / "pishkar", tmp_path / "staging"
    _make_state(base)
    staging.mkdir()

    archive = create_backup_archive(
        base, staging, now=datetime(2026, 7, 3, 12, 30, 45, tzinfo=UTC)
    )
    assert archive.name == "pishkar-backup-20260703-123045.tar.gz"


def test_db_snapshot_is_a_valid_consistent_copy(tmp_path: Path) -> None:
    base, staging = tmp_path / "pishkar", tmp_path / "staging"
    _make_state(base)
    staging.mkdir()

    archive = create_backup_archive(base, staging)
    with tarfile.open(archive) as tar:
        tar.extract("sessions.db", tmp_path / "restored", filter="data")

    conn = sqlite3.connect(tmp_path / "restored" / "sessions.db")
    try:
        rows = conn.execute("SELECT v FROM t").fetchall()
    finally:
        conn.close()
    assert rows == [("keep-me",)]
    # The staging snapshot is cleaned up after archiving.
    assert not (staging / "sessions.db").exists()


def test_empty_base_dir_raises(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    with pytest.raises(FileNotFoundError):
        create_backup_archive(tmp_path / "nothing-here", staging)


async def test_local_dir_backend_copies_and_creates_dest(tmp_path: Path) -> None:
    archive = tmp_path / "pishkar-backup-x.tar.gz"
    archive.write_bytes(b"tar-bytes")
    dest_dir = tmp_path / "nas" / "backups"

    backend = LocalDirBackend(dest_dir)
    assert isinstance(backend, BackupBackend)
    stored = await backend.store(archive)

    assert stored == str(dest_dir / archive.name)
    assert (dest_dir / archive.name).read_bytes() == b"tar-bytes"


# ---- the `backup` tool -----------------------------------------------------


async def test_tool_requires_backup_dir_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PISHKAR_BACKUP_DIR", raising=False)
    result = await backup_tool.backup()
    assert result.startswith("[error]")
    assert "PISHKAR_BACKUP_DIR" in result


async def test_tool_archives_and_stores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path / "pishkar"
    _make_state(base)
    dest = tmp_path / "backups"
    monkeypatch.setattr(backup_tool, "_BASE_DIR", base)
    monkeypatch.setenv("PISHKAR_BACKUP_DIR", str(dest))

    result = await backup_tool.backup()

    assert result.startswith("Backup stored at ")
    stored = list(dest.glob("pishkar-backup-*.tar.gz"))
    assert len(stored) == 1
    with tarfile.open(stored[0]) as tar:
        assert "users/ali/SOUL.md" in tar.getnames()


async def test_tool_reports_empty_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backup_tool, "_BASE_DIR", tmp_path / "missing")
    monkeypatch.setenv("PISHKAR_BACKUP_DIR", str(tmp_path / "backups"))
    result = await backup_tool.backup()
    assert result.startswith("[error]")
