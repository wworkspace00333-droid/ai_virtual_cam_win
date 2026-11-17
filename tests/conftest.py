import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["VCS_DISABLE_GLOBAL_HOTKEYS"] = "1"   # tests must never grab system-wide shortcuts
os.environ["VCS_NO_AUTOSTART"] = "1"             # tests start capture explicitly
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vcstudio.core.paths import AppPaths  # noqa: E402


HANG_DUMP_S = float(os.environ.get("VCS_HANG_DUMP_S", "240"))


@pytest.fixture(scope="session")
def _hang_log():
    import tempfile
    p = Path(os.environ.get("VCS_HANG_LOG") or Path(tempfile.gettempdir()) / "vcs-test-hang.log")
    f = open(p, "w", encoding="utf-8")
    try:
        yield f
    finally:
        f.close()


@pytest.fixture(autouse=True)
def _dump_stacks_if_hung(request, _hang_log):
    """pytest-timeout's watchdog is a Python thread: when a native call hangs while holding the GIL it never runs,
    and the run hangs with no clue (D10). faulthandler's watchdog is a C thread: after HANG_DUMP_S it writes every
    thread's Python stack to %TEMP%\\vcs-test-hang.log (a file: pytest captures stderr). It does not end the process
    — ending it from there was seen to hang in process exit too."""
    import faulthandler
    _hang_log.write(f"\n== {request.node.nodeid}\n")
    _hang_log.flush()
    faulthandler.dump_traceback_later(HANG_DUMP_S, exit=False, file=_hang_log)
    try:
        yield
    finally:
        faulthandler.cancel_dump_traceback_later()


@pytest.fixture(scope="session")
def _shared_ort_cache(tmp_path_factory):
    return tmp_path_factory.mktemp("ort-cache")


@pytest.fixture
def paths(tmp_path, monkeypatch, _shared_ort_cache):
    """A fresh app home per test. Its optimised-model cache goes to one folder shared by the session: each home used
    to get its own 30–80 MB copy of every model it loaded, about 1.5 GB per full run (and pytest keeps three runs)."""
    from vcstudio.ai import onnx_engine
    home = tmp_path / "home"
    real = onnx_engine._cache_file

    def shared(path, cache_dir, provider, opts):
        if Path(cache_dir).is_relative_to(home):
            cache_dir = _shared_ort_cache
        return real(path, cache_dir, provider, opts)

    monkeypatch.setattr(onnx_engine, "_cache_file", shared)
    monkeypatch.setenv("VCSTUDIO_HOME", str(home))
    return AppPaths(home).ensure()


@pytest.fixture(autouse=True)
def _no_leaked_worker_threads():
    """Every test must stop the capture/AI/video threads it starts."""
    import threading
    import time as _t
    before = {t.ident for t in threading.enumerate()}
    yield
    names = ("capture-supervisor", "ai-engine", "video-", "test-pattern")
    end = _t.monotonic() + 3
    while True:
        leaked = [t.name for t in threading.enumerate()
                  if t.ident not in before and t.is_alive() and t.name.startswith(names)]
        if not leaked or _t.monotonic() > end:
            break
        _t.sleep(0.05)
    assert not leaked, f"leaked worker threads: {leaked}"
