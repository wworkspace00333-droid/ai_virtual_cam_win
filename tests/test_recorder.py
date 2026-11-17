"""Recording the output (roadmap P3.1) and instant replay (P3.2): H.264 through Media Foundation without x264,
fallback encoders, timestamps from the frames, never blocking the output thread, the app and the Preview page."""
from __future__ import annotations

import time

import numpy as np
import pytest

av = pytest.importorskip("av")

from vcstudio.core.recorder import InstantReplay, Recorder, open_writer  # noqa: E402


def frame(i, w=640, h=360):
    a = np.zeros((h, w, 4), np.uint8)
    a[..., 3] = 255
    x = (i * 8) % (w - 40)
    a[:, x:x + 40, 1] = 255                   # a moving green bar
    return a


def decode(path):
    with av.open(str(path)) as c:
        v = c.streams.video[0]
        frames = list(c.decode(v))
        return v.codec_context.name, frames


def h264_mf_works(tmp_path) -> bool:
    try:
        c, _, _ = open_writer(tmp_path / "probe.mp4", 64, 64, 30, ("h264_mf",))
        c.close()
        return True
    except RuntimeError:
        return False


def test_records_h264_with_frame_timestamps(tmp_path):
    if not h264_mf_works(tmp_path):
        pytest.skip("no Media Foundation H.264 encoder on this Windows")
    p = tmp_path / "rec.mp4"
    r = Recorder(p, 30, ("h264_mf",), queue_frames=100)
    r.start()
    for i in range(45):
        assert r.push(frame(i), 100.0 + i / 30, block=True)
    st = r.stop()
    assert st.state == "done" and st.encoder == "h264_mf" and st.frames == 45 and st.dropped == 0
    codec, frames = decode(p)
    assert codec == "h264" and len(frames) == 45
    assert (frames[0].width, frames[0].height) == (640, 360)
    assert abs(frames[-1].time - 44 / 30) < 0.01                 # times relative to the first frame
    g = frames[20].to_ndarray(format="bgr24")
    x = 20 * 8
    assert g[180, x + 20, 1] > 200 and g[180, x + 20, 2] < 60     # the bar is where it was drawn


def test_falls_back_to_the_next_encoder(tmp_path):
    p = tmp_path / "rec.mp4"
    r = Recorder(p, 30, ("no_such_encoder", "mpeg4"), queue_frames=100)
    r.start()
    for i in range(10):
        r.push(frame(i), i / 30, block=True)
    st = r.stop()
    assert st.state == "done" and st.encoder == "mpeg4"
    assert decode(p)[0] == "mpeg4"


def test_no_encoder_is_an_error_not_a_crash(tmp_path):
    r = Recorder(tmp_path / "x.mp4", 30, ("no_such_encoder",))
    r.start()
    r.push(frame(0), 0.0, block=True)
    st = r.stop()
    assert st.state == "error" and "no video encoder" in st.error


def test_variable_frame_rate_and_odd_sizes(tmp_path):
    p = tmp_path / "vfr.mp4"
    r = Recorder(p, 30, ("mpeg4",), queue_frames=100)
    r.start()
    times = [0.0, 0.033, 0.066, 1.066, 1.1, 1.1]          # a 1 s pause, and a repeated timestamp
    for i, t in enumerate(times):
        r.push(frame(i, 641, 361), t, block=True)          # odd size: cropped to even for 4:2:0
    r.push(frame(9, 800, 450), 1.2, block=True)            # size change mid-recording: scaled to the first size
    st = r.stop()
    assert st.state == "done" and st.frames == 7
    _, frames = decode(p)
    assert (frames[0].width, frames[0].height) == (640, 360)
    ts = [f.time for f in frames]
    assert ts == sorted(ts) and len(set(ts)) == len(ts)    # strictly increasing
    assert 0.9 < ts[3] - ts[2] < 1.1                       # the pause is kept


def test_push_never_blocks_and_counts_drops(tmp_path):
    r = Recorder(tmp_path / "d.mp4", 30, ("mpeg4",), queue_frames=2)
    r.start()
    big = frame(0, 1920, 1080)
    t0 = time.perf_counter()
    results = [r.push(big, i / 30) for i in range(60)]
    elapsed = time.perf_counter() - t0
    st = r.stop()
    assert elapsed < 1.5                                   # copies only; never waits for the encoder
    assert st.dropped == results.count(False) and st.frames + st.dropped == 60


def test_instant_replay_keeps_only_the_last_seconds(tmp_path):
    rp = InstantReplay(seconds=2.0, max_side=320)
    for i in range(150):                                   # 5 s at 30 fps
        rp.push(frame(i), i / 30)
    assert 1.9 <= rp.held_seconds <= 2.0
    assert 55 <= len(rp) <= 62
    assert rp.memory_bytes() < 3_000_000
    st = rp.save(tmp_path / "replay.mp4", ("mpeg4",))
    assert st.state == "done" and st.frames == len(rp)
    _, frames = decode(tmp_path / "replay.mp4")
    assert frames[0].width == 320 and abs(frames[-1].time - rp.held_seconds) < 0.05
    rp.clear()
    assert rp.save(tmp_path / "none.mp4").state == "error"


def test_instant_replay_worker_drops_instead_of_blocking():
    rp = InstantReplay(seconds=5.0)
    rp.start()
    try:
        big = frame(0, 1920, 1080)
        t0 = time.perf_counter()
        for i in range(90):
            rp.offer(big, i / 30)
        assert time.perf_counter() - t0 < 1.5
        end = time.monotonic() + 10
        while time.monotonic() < end and rp._q.qsize():
            time.sleep(0.05)
        time.sleep(0.2)
        assert len(rp) + rp.dropped == 90 and len(rp) >= 3
    finally:
        rp.stop()
    assert not rp.running


def _wait(cond, secs=10.0):
    end = time.monotonic() + secs
    while time.monotonic() < end and not cond():
        time.sleep(0.05)
    return cond()


def test_app_records_the_processed_output(paths, tmp_path):
    from vcstudio.app.application import AppContext
    ctx = AppContext(paths, configure_logging=False)
    try:
        ctx.settings.set("input.method", "test_pattern")
        ctx.settings.set("output.camera_enabled", False)
        ctx.settings.set("recording.folder", str(tmp_path / "videos"))
        ctx.recording.encoders = ("mpeg4",)
        ctx.start_capture()
        assert _wait(lambda: ctx.pipeline.stats().frames_out > 3)
        path = ctx.recording.start_recording()
        assert path.parent == tmp_path / "videos" and path.name.startswith("recording-")
        assert ctx.recording.recording and len(ctx.pipeline.taps) == 1
        assert _wait(lambda: ctx.recording.recorder.stats.frames >= 20, 15)
        st = ctx.recording.stop_recording()
        assert st.state == "done" and st.frames >= 20 and ctx.pipeline.taps == []
        _, frames = decode(path)
        assert len(frames) == st.frames
        # instant replay through the settings, as the Preview button does
        ctx.settings.set("recording.replay_enabled", True)
        assert ctx.recording.replay is not None and ctx.recording.replay.running
        assert _wait(lambda: ctx.recording.replay.held_seconds > 0.5, 15)
        st = ctx.recording.save_replay()
        assert st.state == "done" and st.frames > 5
        ctx.settings.set("recording.replay_enabled", False)
        assert ctx.recording.replay is None and ctx.pipeline.taps == []
        events = [e["message"] for e in ctx.db.recent_events(50)]
        assert any("recording stopped" in m for m in events) and any("instant replay saved" in m for m in events)
    finally:
        ctx.stop_capture()
        ctx.shutdown()


def test_shutdown_finishes_a_running_recording(paths, tmp_path):
    from vcstudio.app.application import AppContext
    ctx = AppContext(paths, configure_logging=False)
    ctx.settings.set("input.method", "test_pattern")
    ctx.settings.set("output.camera_enabled", False)
    ctx.recording.encoders = ("mpeg4",)
    try:
        ctx.start_capture()
        path = ctx.recording.start_recording(tmp_path / "s.mp4")
        assert _wait(lambda: ctx.recording.recorder.stats.frames >= 5, 15)
    finally:
        ctx.stop_capture()
        ctx.shutdown()
    assert not ctx.recording.recording
    assert len(decode(path)[1]) >= 5                       # a valid, finished file


def test_preview_buttons(qtbot, paths, tmp_path):
    from vcstudio.app.application import AppContext
    from vcstudio.ui.main_window import MainWindow
    ctx = AppContext(paths, configure_logging=False)
    ctx.settings.set("input.method", "test_pattern")
    ctx.settings.set("output.camera_enabled", False)
    ctx.settings.set("recording.folder", str(tmp_path / "v"))
    ctx.recording.encoders = ("mpeg4",)
    w = MainWindow(ctx)
    qtbot.addWidget(w)
    try:
        page = next(p for p in w.pages if p.key == "preview")
        page.save_replay()                                 # replay off: a hint, no thread
        assert page._saving is None and page.rec_note.text()
        ctx.start_capture()
        page.rec_btn.setChecked(True)
        assert ctx.recording.recording
        assert _wait(lambda: ctx.recording.recorder.stats.frames >= 10, 15)
        page.tick()
        assert page.rec_btn.isChecked()
        w.on_action("record_toggle")                       # the hotkey
        assert not ctx.recording.recording and not page.rec_btn.isChecked()
        assert list((tmp_path / "v").glob("recording-*.mp4"))
        page.replay_btn.setChecked(True)
        assert ctx.settings.get("recording.replay_enabled") and ctx.recording.replay is not None
        assert _wait(lambda: ctx.recording.replay.held_seconds > 0.5, 15)
        page.save_replay()
        assert _wait(lambda: not page._saving.is_alive(), 30)
        assert list((tmp_path / "v").glob("replay-*.mp4"))
        page.replay_btn.setChecked(False)
        assert ctx.recording.replay is None
    finally:
        ctx.stop_capture()
        ctx.shutdown()
