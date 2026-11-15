"""Only one app window per data directory (two copies fight over the output camera)."""
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil

from vcstudio.core import single_instance

ROOT = Path(__file__).resolve().parents[1]


def _pids(p: subprocess.Popen) -> set[int]:
    """The process and its children. On Windows a venv's python.exe is a launcher that runs the real
    interpreter as a child process, so the pid that takes the lock is the child's."""
    try:
        return {p.pid} | {c.pid for c in psutil.Process(p.pid).children(recursive=True)}
    except psutil.NoSuchProcess:
        return {p.pid}


def _kill_tree(p: subprocess.Popen) -> None:
    for pid in _pids(p) - {p.pid}:
        try:
            psutil.Process(pid).kill()
        except psutil.NoSuchProcess:
            pass
    p.kill()
    p.wait()


def test_lock_is_exclusive_and_released(tmp_path):
    holder = subprocess.Popen([sys.executable, "-c",
                               "import sys,time; sys.path.insert(0, sys.argv[1]);"
                               "from pathlib import Path; from vcstudio.core import single_instance as s;"
                               "assert s.acquire(Path(sys.argv[2])) is None; print('held', flush=True); time.sleep(30)",
                               str(ROOT), str(tmp_path)], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        assert single_instance.acquire(tmp_path) in _pids(holder)      # the other process, by pid
    finally:
        _kill_tree(holder)
    # the OS dropped the dead process's lock: a new start is not blocked by a stale file
    end = time.time() + 5
    got = single_instance.acquire(tmp_path)
    while got is not None and time.time() < end:      # Windows releases a dead process's locks asynchronously
        time.sleep(0.1)
        got = single_instance.acquire(tmp_path)
    assert got is None
    single_instance.release(tmp_path)


def test_second_gui_copy_exits(tmp_path):
    env = dict(os.environ, VCSTUDIO_HOME=str(tmp_path), QT_QPA_PLATFORM="offscreen", VCS_DISABLE_GLOBAL_HOTKEYS="1",
               VCS_NO_AUTOSTART="1")
    first = subprocess.Popen([sys.executable, "-m", "vcstudio"], cwd=ROOT, env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        lock = tmp_path / "app.lock"
        end = time.time() + 30
        holder = lambda: lock.exists() and lock.read_text().strip().isdigit() and \
            int(lock.read_text().strip()) in _pids(first)
        while time.time() < end and not holder():
            time.sleep(0.2)
        assert holder()
        pid = lock.read_text().strip()
        second = subprocess.run([sys.executable, "-m", "vcstudio", "--smoke-test"], cwd=ROOT, env=env,
                                capture_output=True, text=True, timeout=60)
        assert second.returncode == 3 and pid in second.stderr
    finally:
        _kill_tree(first)
