"""One app window per data directory.

Two running copies would both send frames to the same output camera and fight over it (found live:
the Card for a game calibration saw the other copy's video instead of its markers). An exclusive
lock on <data dir>/app.lock is held for the life of the process; the OS releases it if the app
crashes, so a stale lock never blocks the next start. Command-line tools (self-check, soak, scripts)
do not take it.

macOS: fcntl.flock. Windows: msvcrt.locking on one byte far past the end of the file. Byte-range
locks are mandatory on Windows, so locking the pid itself would stop another process from reading
it; a lock beyond EOF is allowed and leaves the file's contents readable.
"""
from __future__ import annotations

import os
from pathlib import Path

from vcstudio.platform import IS_WINDOWS

_held: dict[str, int] = {}
LOCK_OFFSET = 1 << 30      # Windows: the locked byte (past EOF; the file itself stays a few bytes long)


def acquire(root: Path) -> int | None:
    """Take the lock for `root`. Returns None if this process holds it now, else the other app's pid
    (0 if unknown)."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / "app.lock"
    return _acquire_win(path) if IS_WINDOWS else _acquire_posix(path)


def release(root: Path) -> None:
    fd = _held.pop(str(root / "app.lock"), None)
    if fd is None:
        return
    if IS_WINDOWS:
        import msvcrt
        try:
            os.lseek(fd, LOCK_OFFSET, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


def _read_pid(path: Path) -> int:
    try:
        return int(path.read_bytes().decode().strip() or 0)
    except (OSError, ValueError):
        return 0


# ------------------------------------------------------------------------------------------ posix
def _acquire_posix(path: Path) -> int | None:
    import fcntl
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        try:
            other = int(os.pread(fd, 32, 0).decode().strip() or 0)
        except (ValueError, OSError):
            other = 0
        os.close(fd)
        return other
    os.ftruncate(fd, 0)
    os.pwrite(fd, str(os.getpid()).encode(), 0)
    _held[str(path)] = fd
    return None


# ---------------------------------------------------------------------------------------- windows
def _acquire_win(path: Path) -> int | None:
    import msvcrt
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_BINARY, 0o644)
    try:
        os.lseek(fd, LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    except OSError:
        os.close(fd)
        return _read_pid(path)
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, str(os.getpid()).encode())
    _held[str(path)] = fd
    return None
