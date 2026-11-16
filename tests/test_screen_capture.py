"""Screen / window capture (Windows): backend fallback, the input source, settings and the Input page.

The live test needs a desktop that is being drawn: in a disconnected remote desktop session every backend fails
(GDI: access denied, WGC: no frames, Desktop Duplication: no outputs) and it is skipped."""
from __future__ import annotations

import sys
import threading
import time

import numpy as np
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows")


class FakeGrabber:
    def __init__(self, backend, frames=None, fail_after=None):
        self.backend = backend
        self.frames = frames          # None: never a picture; else returns this picture every 3rd read
        self.fail_after = fail_after
        self.reads = 0
        self.closed = False
        self.thread = None

    def read(self, timeout_ms):
        self.reads += 1
        self.thread = threading.current_thread()
        if self.fail_after is not None and self.reads > self.fail_after:
            from vcstudio.core.video_capture import CaptureError
            raise CaptureError("window closed")
        time.sleep(min(timeout_ms, 5) / 1000)
        if self.frames is None or self.reads % 3:
            return None
        return self.frames

    def close(self):
        self.closed = True


def test_targets_are_parsed_with_safe_defaults():
    from vcstudio.platform.win import screen_capture as sc
    assert sc.parse_target(None) == ("monitor", 0)
    assert sc.parse_target("monitor:2") == ("monitor", 2)
    assert sc.parse_target("monitor:-1") == ("monitor", 0)
    assert sc.parse_target("monitor:x") == ("monitor", 0)
    assert sc.parse_target("window:Notepad: a:b") == ("window", "Notepad: a:b")
    assert sc.parse_target("window:") == ("monitor", 0)
    assert sc.describe("window:Game") == "window “Game”" and sc.describe("monitor:1") == "screen 2"


def test_open_grabber_skips_backends_that_fail_or_give_no_picture(monkeypatch):
    from vcstudio.core.video_capture import CaptureError
    from vcstudio.platform.win import screen_capture as sc
    pic = np.full((4, 6, 4), 7, np.uint8)
    made = {}

    def make(backend, kind, arg):
        if backend == "wgc":
            raise CaptureError("not supported")
        g = FakeGrabber(backend, None if backend == "dupl" else pic)
        made[backend] = g
        return g

    monkeypatch.setattr(sc, "_make", make)
    g, first, notes = sc.open_grabber("monitor:0", first_frame_s=0.2)
    assert g.backend == "gdi" and first.shape == (4, 6, 4) and first is not pic       # a copy
    assert notes[0].startswith("wgc: not supported") and notes[1].startswith("dupl: no picture")
    assert made["dupl"].closed and not made["gdi"].closed
    monkeypatch.setattr(sc, "_make", lambda b, k, a: FakeGrabber(b))
    with pytest.raises(CaptureError, match="screen capture failed"):
        sc.open_grabber("monitor:0", first_frame_s=0.05)


def test_window_target_that_is_not_open_is_reported(monkeypatch):
    from vcstudio.core.video_capture import CaptureError
    from vcstudio.platform.win import screen_capture as sc
    monkeypatch.setattr(sc, "windows", lambda include_own=False: [sc.Window(1, "Some Game", 5)])
    assert sc.find_window("some game").hwnd == 1 and sc.find_window("game").hwnd == 1
    with pytest.raises(CaptureError, match="no open window"):
        sc.open_grabber("window:Nothing like this")


def test_source_repeats_the_last_picture_and_its_thread_owns_the_grabber(monkeypatch):
    from vcstudio.platform.win import screen_capture as sc
    pic = np.zeros((8, 10, 4), np.uint8)
    g = FakeGrabber("wgc", pic)
    monkeypatch.setattr(sc, "open_grabber", lambda target, backends: (g, pic.copy(), ["dupl: skipped"]))
    frames, events = [], []
    src = sc.ScreenCaptureSource("monitor:0", fps=20)
    src.start(frames.append, lambda *a: events.append(a))
    time.sleep(0.6)
    src.stop()
    assert src.backend == "wgc" and src.nominal == (10, 8, 20.0)
    assert 8 <= len(frames) <= 16, len(frames)             # ~20 fps whether or not the screen changed
    assert frames[0].width == 10 and frames[0].height == 8 and frames[-1].seq == len(frames)
    assert g.closed and g.thread is not threading.main_thread() and not events


def test_source_reports_a_lost_window_and_closes(monkeypatch):
    from vcstudio.platform.win import screen_capture as sc
    pic = np.zeros((8, 10, 4), np.uint8)
    g = FakeGrabber("wgc", pic, fail_after=5)
    monkeypatch.setattr(sc, "open_grabber", lambda target, backends: (g, pic.copy(), []))
    events = []
    src = sc.ScreenCaptureSource("window:Game", fps=30)
    src.start(lambda f: None, lambda *a: events.append(a))
    end = time.monotonic() + 2
    while not events and time.monotonic() < end:
        time.sleep(0.02)
    src.stop()
    assert events and events[0][0] == "disconnected" and "window closed" in events[0][1]
    assert g.closed


def test_gdi_source_polls_once_per_frame(monkeypatch):
    from vcstudio.platform.win import screen_capture as sc
    pic = np.zeros((4, 4, 4), np.uint8)

    class Poll(FakeGrabber):
        def read(self, timeout_ms):
            self.reads += 1
            return pic

    g = Poll("gdi")
    monkeypatch.setattr(sc, "open_grabber", lambda target, backends: (g, pic.copy(), []))
    src = sc.ScreenCaptureSource("monitor:0", fps=10)
    src.start(lambda f: None, lambda *a: None)
    time.sleep(0.55)
    src.stop()
    assert 3 <= g.reads <= 8, g.reads                       # not a busy loop of full-screen copies


def test_settings_and_capture_factory_use_the_screen_method(paths):
    from vcstudio.app.application import AppContext
    from vcstudio.platform.win.screen_capture import ScreenCaptureSource
    ctx = AppContext(paths, configure_logging=False)
    try:
        ctx.settings.set("input.method", "screen")
        ctx.settings.set("input.screen_target", "window:Game")
        ctx.settings.set("input.preferred_fps", 30)
        src = ctx.capture_factory()()
        assert isinstance(src, ScreenCaptureSource) and src.target == "window:Game" and src.fps == 30
        assert ctx.feedback_loop_risk() == ""
    finally:
        ctx.shutdown()


def test_input_page_lists_screens_and_windows_for_the_screen_method(qtbot, paths, monkeypatch):
    from vcstudio.app.application import AppContext
    from vcstudio.platform.win import screen_capture as sc
    from vcstudio.ui.main_window import MainWindow
    monkeypatch.setattr(sc, "monitors", lambda: [sc.Monitor(0, 0, 0, 1920, 1080), sc.Monitor(1, 1920, 0, 1280, 1024)])
    monkeypatch.setattr(sc, "windows", lambda include_own=False: [sc.Window(5, "Browser game", 1)])
    ctx = AppContext(paths, configure_logging=False)
    ctx.settings.set("input.screen_target", "window:Browser game")
    w = MainWindow(ctx)
    qtbot.addWidget(w)
    try:
        page = w.input_page
        page.method.setCurrentIndex(page.method.findData("screen"))
        items = [page.device.itemData(i) for i in range(page.device.count())]
        assert items == ["monitor:0", "monitor:1", "window:Browser game"]
        assert page.device.currentData() == "window:Browser game" and page.device.isEnabled()
        page.device.setCurrentIndex(1)
        assert page.apply_to_settings() and ctx.settings.get("input.screen_target") == "monitor:1"
        page.method.setCurrentIndex(page.method.findData("test_pattern"))
        assert "monitor:0" not in [page.device.itemData(i) for i in range(page.device.count())]
    finally:
        ctx.shutdown()


def test_live_screen_grab_when_a_desktop_is_drawn():
    from vcstudio.core.video_capture import CaptureError
    from vcstudio.platform.win import screen_capture as sc
    try:
        img, backend = sc.grab(f"monitor:{sc.primary_index()}")
    except CaptureError as e:
        pytest.skip(f"no drawn desktop in this session ({e})")
    m = sc.monitors()[sc.primary_index()]
    assert backend in sc.BACKENDS and img.ndim == 3 and img.shape[2] == 3
    assert abs(img.shape[1] / img.shape[0] - m.width / m.height) < 0.05
    # every backend that works here gives a real picture, not the empty first surface (Desktop Duplication once did)
    for b in sc.BACKENDS:
        try:
            pic, _ = sc.grab(f"monitor:{sc.primary_index()}", (b,))
        except CaptureError:
            continue
        assert pic.std() > 1.0, f"{b}: blank picture"
