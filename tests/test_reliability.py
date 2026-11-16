"""Thread lifecycle under watchdog recovery and shutdown (audit docs/AUDIT_WIN.md, R1-R3)."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np

from vcstudio.core.frame import Frame, host_time_s
from vcstudio.core.frame_queue import LatestSlot


class _Feed:
    """Minimal capture: a LatestSlot fed with small frames from a thread."""

    def __init__(self):
        self.slot: LatestSlot[Frame] = LatestSlot()
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        seq = 0
        img = np.zeros((90, 160, 4), np.uint8)
        while not self._stop.is_set():
            seq += 1
            t = host_time_s()
            self.slot.put(Frame(seq, t, t, 160, 90, array=img, source="test"))
            time.sleep(0.01)

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *a):
        self._stop.set()
        self._t.join(2)


class _Detector:
    def __init__(self, name, gate: threading.Event | None = None):
        self.name, self.gate, self.calls = name, gate, 0

    def detect(self, frame, conf, iou):
        self.calls += 1
        if self.gate is not None:
            self.gate.wait(10)             # a model call that hangs until released
        return [], 1.0


def _ai_threads():
    return [t for t in threading.enumerate() if t.name == "ai-engine" and t.is_alive()]


def test_ai_restart_does_not_leave_the_abandoned_worker_running(tmp_path: Path):
    from vcstudio.ai.engine import AIEngine
    from vcstudio.ai.model_manager import ModelManager
    gate = threading.Event()
    stuck, fresh = _Detector("stuck", gate), _Detector("fresh")
    detectors = iter([stuck, fresh])
    with _Feed() as feed:
        eng = AIEngine(feed, ModelManager(tmp_path))

        def ensure(cfg, mode):
            if eng._detector is None:
                eng._detector = next(detectors)
        eng._ensure_models = ensure
        eng.start()
        try:
            end = time.monotonic() + 5
            while stuck.calls == 0 and time.monotonic() < end:
                time.sleep(0.01)
            assert stuck.calls == 1                       # the first worker is now inside a hung model call
            eng.restart("test: hung inference")
            end = time.monotonic() + 5
            while fresh.calls < 3 and time.monotonic() < end:
                time.sleep(0.01)
            assert fresh.calls >= 3                       # the new worker runs
            gate.set()                                    # the hung call returns
            time.sleep(0.3)
            assert stuck.calls == 1                       # ... and the abandoned worker does not continue
            assert len(_ai_threads()) == 1
            res = eng.results.peek()[0]
            assert res is not None and res.model == "fresh"
        finally:
            gate.set()
            eng.stop()
    assert not _ai_threads()


class _HangingMfDevice:
    """Stands in for MfCapture: read() can hang; records which thread closes it and whether a read was running."""
    TIMEOUT, LOST, FRAME = 0, -1, 1
    width, height = 32, 16

    def __init__(self):
        self.gate = threading.Event()
        self.reading = threading.Event()
        self.closed_by: list[str] = []
        self.closed_while_reading = False
        self._in_read = False

    def read(self, out, timeout_ms=200):
        self._in_read = True
        self.reading.set()
        self.gate.wait(10)
        self._in_read = False
        return self.TIMEOUT

    def close(self):
        self.closed_while_reading |= self._in_read
        self.closed_by.append(threading.current_thread().name)


def test_camera_controller_shutdown_waits_for_a_send_and_refuses_later_ones(monkeypatch):
    import vcstudio.camera.output as output
    from vcstudio.core.video_pipeline import CameraOutputController
    in_send, release = threading.Event(), threading.Event()
    opened, closed = [], []

    class FakeOut:
        def __init__(self, *a):
            opened.append(self)
            self.sending = False

        def open(self):
            pass

        def send(self, pb, pts):
            self.sending = True
            in_send.set()
            release.wait(5)
            self.sending = False
            return True

        def close(self):
            closed.append(self.sending)        # True would mean: closed in the middle of a send

        def stats(self):
            return (0, 0)
    monkeypatch.setattr(output, "VirtualCameraOutput", FakeOut)
    cam = CameraOutputController("AI Virtual Camera", "", True)
    t = threading.Thread(target=cam.send, args=(None, 0.0))
    t.start()
    assert in_send.wait(2)
    done = threading.Event()
    threading.Thread(target=lambda: (cam.shutdown(), done.set())).start()
    time.sleep(0.2)
    assert not done.is_set()                    # shutdown waits for the send in progress
    release.set()
    assert done.wait(2) and closed == [False]
    t.join(2)
    assert cam.send(None, 0.0) is None and len(opened) == 1     # a late send does not reopen the camera
    cam.resume()
    assert cam.send(None, 0.0) is True and len(opened) == 2
    cam.close()


def _sddl_of(handle) -> str:
    import ctypes
    from ctypes import wintypes
    adv, k32 = ctypes.WinDLL("advapi32"), ctypes.WinDLL("kernel32")
    adv.GetSecurityInfo.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD] + [ctypes.POINTER(ctypes.c_void_p)] * 4 \
        + [ctypes.POINTER(ctypes.c_void_p)]
    adv.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR), ctypes.c_void_p]
    k32.LocalFree.argtypes = [ctypes.c_void_p]
    sd = ctypes.c_void_p()
    assert adv.GetSecurityInfo(handle, 6, 0x4 | 0x1, None, None, None, None, ctypes.byref(sd)) == 0   # DACL | OWNER
    s = wintypes.LPWSTR()
    assert adv.ConvertSecurityDescriptorToStringSecurityDescriptorW(sd, 1, 0x4 | 0x1, ctypes.byref(s), None)
    try:
        return s.value
    finally:
        k32.LocalFree(s); k32.LocalFree(sd)


def _windows_only():
    import sys
    if sys.platform != "win32":
        import pytest
        pytest.skip("Windows")


def test_camera_shared_memory_is_readable_only_by_this_user_and_the_camera_service():
    _windows_only()
    import os
    from vcstudio.platform.win.vcam_writer import VcamWriter, token_sids
    user, _ = token_sids()
    w = VcamWriter(f"Local\\vcs-test-dacl-{os.getpid()}")
    try:
        sddl = _sddl_of(w.h)
        assert ";;;WD)" not in sddl and "AU)" not in sddl and ";;;BU)" not in sddl   # not Everyone / all users
        me = "LA" if user.endswith("-500") else user                           # the built-in Administrator is abbreviated
        assert f";;;{me})" in sddl and ";;;LS)" in sddl                           # this user, Frame Server
        assert sddl.count("(A;") == 4                                             # user, LS, app containers, restricted
    finally:
        w.close()


def test_camera_shared_memory_created_by_someone_else_is_refused(monkeypatch):
    _windows_only()
    import os
    import pytest
    import vcstudio.platform.win.vcam_writer as vw
    name = f"Local\\vcs-test-squat-{os.getpid()}"
    squatter = vw.VcamWriter(name)                       # stands in for an object created beforehand
    try:
        monkeypatch.setattr(vw, "token_sids", lambda: ("S-1-5-21-1-2-3-1001", "S-1-5-21-1-2-3-1001"))
        with pytest.raises(PermissionError):
            vw.VcamWriter(name)
        monkeypatch.undo()
        again = vw.VcamWriter(name)                      # the same user (a second copy, a test) may share it
        again.close()
    finally:
        squatter.close()


def test_capture_stop_never_closes_a_device_that_is_still_being_read(monkeypatch):
    import sys
    if sys.platform != "win32":
        import pytest
        pytest.skip("Windows capture")
    from vcstudio.platform.win.capture import WindowsCameraSource
    src = WindowsCameraSource("mediafoundation", "x", "Test camera")
    dev = _HangingMfDevice()
    src._cap = dev
    src._on_frame = lambda f: None
    src._launch(src._run_mf, dev, "capture-test")
    assert dev.reading.wait(2)
    t = src._thread
    monkeypatch.setattr(t, "join", lambda timeout=None: None)     # stop() gives up waiting at once
    src.stop()
    assert dev.closed_by == []                     # not closed under the running read
    dev.gate.set()                                 # the read returns ...
    threading.Thread.join(t, 2)                    # (t.join itself is patched above)
    assert not t.is_alive()
    assert dev.closed_by == ["capture-test"] and not dev.closed_while_reading   # ... and the thread closed it


def test_capture_restart_gives_the_new_thread_its_own_stop_event():
    import sys
    if sys.platform != "win32":
        import pytest
        pytest.skip("Windows capture")
    from vcstudio.platform.win.capture import WindowsCameraSource
    src = WindowsCameraSource("mediafoundation", "x", "Test camera")
    old, new = _HangingMfDevice(), _HangingMfDevice()
    src._on_frame = lambda f: None
    src._launch(src._run_mf, old, "capture-old")
    old_stop = src._stop
    assert old.reading.wait(2)
    src._launch(src._run_mf, new, "capture-new")   # what a restart after an abandoned stop does
    old_stop.set()
    assert not src._stop.is_set()                  # the new thread is not stopped by the old event ...
    old.gate.set()
    time.sleep(0.2)
    assert old.closed_by == ["capture-old"]        # ... and the old one ended and closed only its own device
    assert new.closed_by == []
    src._stop.set(); new.gate.set()
    time.sleep(0.2)
    assert new.closed_by == ["capture-new"]
