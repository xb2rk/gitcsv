"""Pre-pull CSV backups. Microsecond timestamps avoid same-second
collisions (v1 used second precision); keeps newest N with prune +
list/restore for the CLI `stash` surface."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

KEEP_DEFAULT = 20


def _timestamp() -> str:
    # Seconds + zero-padded sub-second nanos: lexicographic order ==
    # chronological order (v1 used bare seconds and collided; a bare
    # microsecond fraction would wrap every millisecond).
    return time.strftime("%Y%m%dT%H%M%S") + f"{time.time_ns() % 1_000_000_000:09d}"


def backup_file(source: Path, backup_root: Path) -> Path | None:
    if not source.exists():
        return None
    backup_root.mkdir(parents=True, exist_ok=True)
    dest = backup_root / f"{source.stem}_{_timestamp()}{source.suffix}"
    shutil.copy2(source, dest)
    prune_backups(backup_root, source.stem)
    return dest


def list_backups(backup_root: Path, stem: str = "") -> list[Path]:
    if not backup_root.is_dir():
        return []
    pattern = f"{stem}_*" if stem else "*"
    return sorted(backup_root.glob(pattern), reverse=True)


def prune_backups(backup_root: Path, stem: str, *, keep: int = KEEP_DEFAULT) -> int:
    """Delete oldest beyond `keep`. Returns removed count."""
    entries = sorted(backup_root.glob(f"{stem}_*"))
    removed = 0
    for path in entries[: max(0, len(entries) - keep)]:
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def restore_backup(backup: Path, dest: Path) -> None:
    shutil.copy2(backup, dest)
