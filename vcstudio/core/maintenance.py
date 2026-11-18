"""Maintenance isolation: one-off jobs the user starts (or the first start runs) are kept apart from the live video.

Jobs: detector benchmark, accuracy check, model import check, automatic performance measurement, model downloads,
saving an instant replay. Without isolation they ran as ordinary threads next to the camera: two benchmarks could
run at once, and the resource scheduler took the slowdown they caused as "the PC is overloaded" and throttled the
AI modules, which then stayed throttled for many seconds after the job ended.

Each job here
  - runs in its own thread named "maint-<name>" with lowered OS priority (Windows: below normal), so the capture,
    output and AI threads win the CPU when both want it;
  - heavy jobs (CPU-bound measurements) run one at a time; a second one waits for the first;
  - is listed while it runs (`running()`), so the scheduler holds its decisions ("maintenance" on the
    Performance page) instead of throttling live modules for a temporary job, and the status shows why the
    output rate dipped;
  - never raises into the caller: errors are logged and passed to the job's own handler.

Limits: worker threads that a job's ONNX Runtime session creates keep normal priority (ONNX Runtime has no option
for it); the one-at-a-time rule and the scheduler hold are what protect the video from those.
"""
from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable

log = logging.getLogger(__name__)

_lock = threading.Lock()
_heavy = threading.Lock()
_running: dict[int, str] = {}           # job id -> name
_next_id = 0


def lower_thread_priority() -> bool:
    """Give the calling thread below-normal OS priority. True if it was changed."""
    if sys.platform != "win32":         # pragma: macos - per-thread priority is a Windows API; POSIX nice is per process
        return False
    import ctypes
    k32 = ctypes.WinDLL("kernel32")
    k32.GetCurrentThread.restype = ctypes.c_void_p
    k32.SetThreadPriority.argtypes = (ctypes.c_void_p, ctypes.c_int)
    THREAD_PRIORITY_BELOW_NORMAL = -1
    return bool(k32.SetThreadPriority(k32.GetCurrentThread(), THREAD_PRIORITY_BELOW_NORMAL))


def thread_priority() -> int | None:
    """OS priority of the calling thread (Windows; None elsewhere). For tests and diagnostics."""
    if sys.platform != "win32":         # pragma: macos
        return None
    import ctypes
    k32 = ctypes.WinDLL("kernel32")
    k32.GetCurrentThread.restype = ctypes.c_void_p
    k32.GetThreadPriority.argtypes = (ctypes.c_void_p,)
    return int(k32.GetThreadPriority(k32.GetCurrentThread()))


def running() -> list[str]:
    """Names of the jobs running now (waiting heavy jobs are included: they are about to run)."""
    with _lock:
        return list(_running.values())


def busy() -> str:
    """Running job names joined for a status line; "" when none (the scheduler's maintenance probe)."""
    return ", ".join(running())


def start(name: str, fn: Callable[[], object], *, heavy: bool = True,
          on_error: Callable[[BaseException], None] | None = None) -> threading.Thread:
    """Run `fn` as a maintenance job in a new daemon thread and return the thread (already started)."""
    global _next_id
    with _lock:
        _next_id += 1
        jid = _next_id
        _running[jid] = name             # listed from now on, so a status refresh right after start sees it

    def body():
        try:
            lower_thread_priority()
            if heavy:
                with _heavy:
                    fn()
            else:
                fn()
        except BaseException as e:      # noqa: BLE001 - a job must never take the app down
            log.exception("maintenance job %s failed", name)
            if on_error is not None:
                try:
                    on_error(e)
                except Exception:
                    log.exception("maintenance job %s: error handler failed", name)
        finally:
            with _lock:
                _running.pop(jid, None)

    t = threading.Thread(target=body, name=f"maint-{name}", daemon=True)
    try:
        t.start()
    except BaseException:
        with _lock:
            _running.pop(jid, None)
        raise
    return t
