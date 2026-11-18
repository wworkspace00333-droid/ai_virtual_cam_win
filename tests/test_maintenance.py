"""Maintenance isolation (roadmap P2.1): one-off jobs run at lower priority, heavy ones one at a time, are listed
while they run, and make the resource scheduler hold instead of throttling the live AI modules."""
from __future__ import annotations

import sys
import threading
import time

import pytest

from vcstudio.core import maintenance
from vcstudio.core.scheduler import Module, ResourceScheduler


def wait_for(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def test_job_is_listed_while_it_runs_and_gone_after():
    gate = threading.Event()
    t = maintenance.start("t-listed", gate.wait)
    assert "t-listed" in maintenance.running() and "t-listed" in maintenance.busy()
    assert t.name == "maint-t-listed" and t.daemon
    gate.set()
    t.join(5)
    assert "t-listed" not in maintenance.running()


def test_heavy_jobs_run_one_at_a_time_light_ones_alongside():
    lock = threading.Lock()
    now = {"heavy": 0, "max_heavy": 0, "light_during_heavy": False}
    release = threading.Event()

    def heavy():
        with lock:
            now["heavy"] += 1
            now["max_heavy"] = max(now["max_heavy"], now["heavy"])
        release.wait(5)
        time.sleep(0.05)
        with lock:
            now["heavy"] -= 1

    def light():
        with lock:
            now["light_during_heavy"] = now["heavy"] > 0

    a = maintenance.start("t-heavy-a", heavy)
    b = maintenance.start("t-heavy-b", heavy)
    assert wait_for(lambda: now["heavy"] == 1)
    maintenance.start("t-light", light, heavy=False).join(5)
    assert now["light_during_heavy"]              # a download does not wait for a benchmark
    assert set(maintenance.running()) >= {"t-heavy-a", "t-heavy-b"}    # the waiting one is listed too
    release.set()
    a.join(5); b.join(5)
    assert now["max_heavy"] == 1


def test_failing_job_is_contained_and_reported():
    seen = []
    t = maintenance.start("t-fails", lambda: 1 / 0, on_error=seen.append)
    t.join(5)
    assert len(seen) == 1 and isinstance(seen[0], ZeroDivisionError)
    assert "t-fails" not in maintenance.running()
    # a broken error handler is logged, not raised
    maintenance.start("t-fails-2", lambda: 1 / 0, on_error=lambda e: [][0]).join(5)
    assert "t-fails-2" not in maintenance.running()


@pytest.mark.skipif(sys.platform != "win32", reason="per-thread priority is a Windows API")
def test_job_thread_runs_below_normal_priority():
    got = {}
    maintenance.start("t-prio", lambda: got.setdefault("p", maintenance.thread_priority())).join(5)
    assert got["p"] == -1                         # THREAD_PRIORITY_BELOW_NORMAL
    assert maintenance.thread_priority() == 0     # the caller's own priority is untouched


def test_scheduler_holds_while_a_job_runs_then_resumes():
    job = {"name": ""}
    scales = {}
    s = ResourceScheduler(lambda: (40.0, 60.0), maintenance=lambda: job["name"])     # output well below input
    s.add(Module("background", 1, 1 / 3, active=lambda: True, apply=lambda sc, why: scales.__setitem__("bg", sc)))
    job["name"] = "benchmark"
    for i in range(20):                           # 10 s of overload caused by the benchmark
        s.step(i * 0.5)
    assert scales["bg"] == 1.0 and s.status.load == "maintenance" and s.status.steps_down == 0
    assert "benchmark" in s.status.last_action
    job["name"] = ""                              # job done: the timer starts again, no instant step
    s.step(10.0)
    assert scales["bg"] == 1.0 and s.status.load == "overloaded"
    s.step(12.0)
    assert scales["bg"] == 0.75


def test_scheduler_ignores_a_broken_maintenance_probe():
    s = ResourceScheduler(lambda: (60.0, 60.0), maintenance=lambda: [][0])
    s.step(0.0)
    assert s.status.load == "ok"


def test_app_scheduler_watches_maintenance_jobs(tmp_path):
    from vcstudio.app.application import AppContext
    from vcstudio.core.paths import AppPaths
    ctx = AppContext(AppPaths(tmp_path), configure_logging=False)
    try:
        assert ctx.scheduler.maintenance is maintenance.busy
    finally:
        ctx.shutdown()
