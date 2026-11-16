"""A video file as the input: real-time playback, loop or hold, settings, Input page, and the pipeline end to end."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

DATA = Path(__file__).parent / "data"
CLIP = DATA / "clip_h264.mp4"          # 320x180, 30 fps, 2 s

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows decoder (PyAV); macOS uses AVFoundation")


def collect(src, seconds):
    frames, events = [], []
    src.start(frames.append, lambda *a: events.append(a))
    time.sleep(seconds)
    src.stop()
    return frames, events


def test_plays_in_real_time_and_loops():
    from vcstudio.core.video_file_source import VideoFileSource
    src = VideoFileSource(CLIP, loop=True)
    frames, events = collect(src, 2.6)
    assert not events and src.nominal == (320, 180, 30.0)
    assert 60 <= len(frames) <= 85, len(frames)              # ~30 fps over 2.6 s, not as fast as it decodes
    assert src.loops >= 1 and not src.ended
    f = frames[0]
    assert (f.width, f.height) == (320, 180) and f.array.shape == (180, 320, 4) and (f.array[..., 3] == 255).all()
    assert [x.seq for x in frames] == list(range(1, len(frames) + 1))


def test_without_loop_holds_the_last_picture():
    from vcstudio.core.video_file_source import VideoFileSource
    src = VideoFileSource(CLIP, loop=False)
    frames, events = collect(src, 2.8)
    assert src.ended and src.loops == 0 and not events
    assert len(frames) >= 75                                  # keeps delivering after the 2 s clip ended
    assert (frames[-1].array == frames[-2].array).all()


def test_missing_or_unreadable_file_is_reported(tmp_path):
    from vcstudio.core.video_capture import CaptureError
    from vcstudio.core.video_file_source import VideoFileSource
    with pytest.raises(CaptureError, match="not found"):
        VideoFileSource(tmp_path / "nope.mp4").start(lambda f: None, lambda *a: None)
    with pytest.raises(CaptureError, match="none chosen"):
        VideoFileSource("").start(lambda f: None, lambda *a: None)
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(CaptureError):
        VideoFileSource(bad).start(lambda f: None, lambda *a: None)


def test_capture_factory_and_settings(paths):
    from vcstudio.app.application import AppContext
    from vcstudio.core.video_file_source import VideoFileSource
    ctx = AppContext(paths, configure_logging=False)
    try:
        ctx.settings.set("input.method", "video_file")
        ctx.settings.set("input.video_path", str(CLIP))
        ctx.settings.set("input.video_loop", False)
        src = ctx.capture_factory()()
        assert isinstance(src, VideoFileSource) and src.path == CLIP and src.loop is False
        assert ctx.feedback_loop_risk() == ""
    finally:
        ctx.shutdown()


def test_input_page_video_file_row(qtbot, paths):
    from vcstudio.app.application import AppContext
    from vcstudio.ui.main_window import MainWindow
    ctx = AppContext(paths, configure_logging=False)
    w = MainWindow(ctx)
    qtbot.addWidget(w)
    try:
        page = w.input_page
        page.method.setCurrentIndex(page.method.findData("video_file"))
        assert page._form.isRowVisible(page.video_row) and not page._form.isRowVisible(page.device)
        page.choose_video(str(CLIP))
        assert "320×180" in page.video_info.text() and "30" in page.video_info.text()
        page.video_loop.setChecked(False)
        assert page.apply_to_settings()
        assert ctx.settings.get("input.video_path") == str(CLIP) and ctx.settings.get("input.video_loop") is False
        page.method.setCurrentIndex(page.method.findData("test_pattern"))
        assert not page._form.isRowVisible(page.video_row)
    finally:
        ctx.shutdown()


def test_video_file_through_the_pipeline_with_face_hiding(paths):
    """File → AI → compositor → output frames, the way it reaches the camera (camera output itself off here)."""
    from vcstudio.app.application import AppContext
    from vcstudio.roi.model import set_quick_face
    ctx = AppContext(paths, configure_logging=False)
    try:
        ctx.settings.set("input.method", "video_file")
        ctx.settings.set("input.video_path", str(CLIP))
        ctx.settings.set("output.camera_enabled", False)
        ctx.update_active_profile(lambda b: set_quick_face(b, True))
        ctx.start_capture()
        end = time.monotonic() + 10
        while time.monotonic() < end and ctx.pipeline.stats().frames_out < 60:
            time.sleep(0.1)
        st = ctx.pipeline.stats()
        assert st.frames_out >= 60, st
        assert ctx.capture.stats().source_name.startswith("Video file")
    finally:
        ctx.stop_capture()
        ctx.shutdown()
