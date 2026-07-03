"""`backup` tool — archive Pishkar's data and store it off-box.

The destination directory comes from `PISHKAR_BACKUP_DIR` (a mounted
NAS/USB path counts; S3/B2 arrive later behind the same
`BackupBackend` seam). Schedule nightly runs the workspace way: add a
`cron.json` entry whose prompt asks Pishkar to run the backup tool.
"""

import asyncio
import os
import tempfile
from pathlib import Path

from pishkar.tools.registry import tool
from pishkar.workspace.backup import LocalDirBackend, create_backup_archive

_BASE_DIR = Path.home() / ".pishkar"


@tool(
    description=(
        "Create a backup archive of Pishkar's data (session database + "
        "workspace files) and store it in the configured backup location. "
        "Requires PISHKAR_BACKUP_DIR to be set. Returns where the backup "
        "was stored."
    )
)
async def backup() -> str:
    dest = os.environ.get("PISHKAR_BACKUP_DIR")
    if not dest:
        return (
            "[error] PISHKAR_BACKUP_DIR is not set; add it to .env "
            "(any mounted directory works) to enable backups."
        )
    backend = LocalDirBackend(Path(dest))
    with tempfile.TemporaryDirectory() as td:
        try:
            archive = await asyncio.to_thread(
                create_backup_archive, _BASE_DIR, Path(td)
            )
        except FileNotFoundError as e:
            return f"[error] {e}"
        size = archive.stat().st_size
        stored = await backend.store(archive)
    return f"Backup stored at {stored} ({size:,} bytes)."


__all__ = ["backup"]
