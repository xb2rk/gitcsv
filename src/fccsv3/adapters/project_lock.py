"""Keep one v3 write or recovery operation active per project."""

from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO

from fccsv3.adapters.state_db import StateConflict


class ProjectWriteLock:
    """Hold an OS byte lock across reservation, external writes, and commit."""

    def __init__(self, root: Path) -> None:
        self.path = root / ".fccsv3" / "write.lock"
        self.handle: BinaryIO | None = None

    def __enter__(self) -> ProjectWriteLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            handle.close()
            raise StateConflict(
                "another fccsv3 write or recovery is active for this project"
            ) from error
        self.handle = handle
        return self

    def __exit__(self, *_exc: object) -> None:
        handle = self.handle
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            self.handle = None
