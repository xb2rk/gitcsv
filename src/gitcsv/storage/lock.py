"""Project-level file write lock to prevent concurrent modifications."""

from __future__ import annotations

import os
import time
from pathlib import Path


class LockAcquisitionError(Exception):
    """Raised when the write lock cannot be acquired within the timeout."""


class ProjectLock:
    """A cross-platform file-based lock context manager for gitcsv."""

    def __init__(self, project_root: Path, timeout_seconds: float = 10.0) -> None:
        self.lock_dir = project_root / ".gitcsv"
        self.lock_file = self.lock_dir / "write.lock"
        self.timeout = timeout_seconds
        self._fd: int | None = None

    def __enter__(self) -> ProjectLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.release()

    def acquire(self) -> None:
        """Acquire the lock exclusively or raise LockAcquisitionError on timeout."""
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + self.timeout

        while True:
            try:
                # O_CREAT | O_EXCL is atomic across Windows and POSIX
                self._fd = os.open(self.lock_file, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                # Write current process ID and timestamp
                os.write(self._fd, f"pid={os.getpid()} time={time.time()}".encode("utf-8"))
                return
            except FileExistsError:
                if time.time() >= deadline:
                    raise LockAcquisitionError(
                        f"Another gitcsv process is currently writing. Lock held: {self.lock_file}\n"
                        "Wait for it to finish, or remove the lock file if the process died."
                    )
                time.sleep(0.1)

    def release(self) -> None:
        """Release and remove the lock file."""
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

        try:
            if self.lock_file.is_file():
                self.lock_file.unlink()
        except OSError:
            pass
