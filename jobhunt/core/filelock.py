"""One non-blocking cross-process file lock (`fcntl` on Linux and macOS, `msvcrt` on Windows).

On Windows the locked byte is the one at the file's current position, so every call seeks to offset 0
first: the lock then covers the same byte whatever the file's size.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import IO, Any

if sys.platform == "win32":
    import msvcrt

    def lock(handle: IO[Any]) -> None:
        """Takes the lock without waiting; raises `OSError` when another process holds it."""
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def unlock(handle: IO[Any]) -> None:
        """Releases a lock taken with `lock`."""
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def lock(handle: IO[Any]) -> None:
        """Takes the lock without waiting; raises `OSError` when another process holds it."""
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def unlock(handle: IO[Any]) -> None:
        """Releases a lock taken with `lock`."""
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def try_lock(path: Path) -> IO[str] | None:
    """Opens `path` (creating its folder) and locks it without waiting; None when another process holds it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    file = open(path, "a+")
    try:
        lock(file)
    except OSError:
        file.close()
        return None
    return file
