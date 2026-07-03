"""Backup — snapshot the butler's irreplaceable state to a backend.

`BackupBackend` is the pre-paid seam: S3 / B2 land later as new classes
implementing the same Protocol. `LocalDirBackend` is the day-one
implementation — any mounted path counts (NAS share, USB drive, synced
folder).

`create_backup_archive` builds a tar.gz containing a *consistent*
snapshot of `sessions.db` taken with SQLite's online backup API (safe
while the server is writing) plus the `users/` workspace tree and the
top-level config JSONs (`cron.json`, `webhooks.json`). Regenerable
assets (piper voices, logs) are deliberately excluded. Retention is the
destination's problem — the archive names sort chronologically.
"""

import asyncio
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class BackupBackend(Protocol):
    async def store(self, archive: Path) -> str:
        """Persist `archive`; return a human-readable destination."""
        ...


class LocalDirBackend:
    """Copy archives into a destination directory (NAS mount, USB, …)."""

    def __init__(self, dest_dir: Path) -> None:
        self._dest_dir = Path(dest_dir)

    async def store(self, archive: Path) -> str:
        def _copy() -> str:
            self._dest_dir.mkdir(parents=True, exist_ok=True)
            dest = self._dest_dir / archive.name
            shutil.copy2(archive, dest)
            return str(dest)

        return await asyncio.to_thread(_copy)


def _snapshot_db(src: Path, dst: Path) -> None:
    """Consistent copy of a (possibly live) SQLite db via the backup API."""
    conn = sqlite3.connect(src)
    try:
        out = sqlite3.connect(dst)
        try:
            conn.backup(out)
        finally:
            out.close()
    finally:
        conn.close()


def create_backup_archive(
    base_dir: Path, staging_dir: Path, *, now: datetime | None = None
) -> Path:
    """Build `pishkar-backup-<UTC timestamp>.tar.gz` in `staging_dir`.

    Synchronous by design (tarfile + sqlite3 are sync); call it via
    `asyncio.to_thread` from async code. Raises `FileNotFoundError`
    when there is nothing under `base_dir` worth backing up.
    """
    import tarfile

    base_dir = Path(base_dir)
    staging_dir = Path(staging_dir)
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")
    archive_path = staging_dir / f"pishkar-backup-{stamp}.tar.gz"

    db = base_dir / "sessions.db"
    users = base_dir / "users"
    configs = [base_dir / "cron.json", base_dir / "webhooks.json"]
    if not db.is_file() and not users.is_dir():
        raise FileNotFoundError(f"nothing to back up under {base_dir}")

    db_snapshot = staging_dir / "sessions.db"
    with tarfile.open(archive_path, "w:gz") as tar:
        if db.is_file():
            _snapshot_db(db, db_snapshot)
            tar.add(db_snapshot, arcname="sessions.db")
        if users.is_dir():
            tar.add(users, arcname="users")
        for config in configs:
            if config.is_file():
                tar.add(config, arcname=config.name)
    if db_snapshot.exists():
        db_snapshot.unlink()
    return archive_path


__all__ = ["BackupBackend", "LocalDirBackend", "create_backup_archive"]
