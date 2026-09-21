"""Timestamped local backups before any overwrite."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path


def backup_file(source: Path, backup_root: Path) -> Path | None:
    if not source.exists():
        return None
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_root.mkdir(parents=True, exist_ok=True)
    dest = backup_root / f"{source.stem}_{stamp}{source.suffix}"
    shutil.copy2(source, dest)
    return dest
