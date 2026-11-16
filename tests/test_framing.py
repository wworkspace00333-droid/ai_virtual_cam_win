"""Auto-framing (roadmap P2.5): crop to the people with the output's aspect, follow smoothly, hold still in the dead
zone, return to the full frame when nobody is there; the pipeline outputs the framed picture."""
from __future__ import annotations

import time

import numpy as np
import pytest

from vcstudio.ai.tracking import TrackedObject
from vcstudio.replacement.framing import AutoFramer, FramingConfig

W, H = 1920, 1080


def person(x0, y0, x1, y1, tid=1, label="person"):
    return TrackedObject(tid, label, 0.9, x0, y0, x1, y1, "tracked", 1.0, 0.0)


def test_target_has_the_output_aspect_and_respects_zoom_and_bounds():
    f = AutoFramer(FramingConfig(enabled=True, zoom_max=2.0, margin=0.25))
    x, y, w, h = f.target([person(900, 300, 1000, 700)], W, H)
    assert abs(w / h - W / H) < 1e-6                       # 16:9 like the output
    assert w >= W / 2 - 1e-6                               # never closer than zoom_max
    assert 0 <= x and x + w <= W and 0 <= y and y + h <= H
    x, y, w, h = f.target([person(0, 0, 300, 1080), person(1700, 0, 1920, 1080, 2)], W, H)
    assert (w, h) == (W, H)                                # people at both edges: the whole frame
    assert f.target([], W, H) is None
    fx = f.target([person(900, 300, 980, 380, label="face")], W, H)
    assert fx is not None and fx[2] >= W / 2 - 1e-6        # a face alone: head and shoulders


def test_motion_is_smooth_and_frame_rate_independent():
    tgt = [person(1400, 400, 1600, 900)]
    a, b = AutoFramer(FramingConfig(enabled=True, tau=0.6)), AutoFramer(FramingConfig(enabled=True, tau=0.6))
    a.update([], W, H, 0.0); b.update([], W, H, 0.0)       # start at the full frame
    for i in range(1, 31):
        a.update(tgt, W, H, i / 30)                        # 30 fps
    for i in range(1, 61):
        b.update(tgt, W, H, i / 60)                        # 60 fps
    assert np.allclose(a.rect, b.rect, atol=8)             # same place after 1 s at either rate
    want = a.target(tgt, W, H)
    assert abs(a.rect[2] - want[2]) < 0.25 * (W - want[2]) # most of the way there after ~1.7 tau


def test_dead_zone_and_return_to_full_frame():
    f = AutoFramer(FramingConfig(enabled=True, tau=0.2, lost_s=1.0))
    tgt = [person(900, 300, 1100, 800)]
    t = 0.0
    f.update(tgt, W, H, t)
    for _ in range(100):
        t += 1 / 30
        f.update(tgt, W, H, t)
    settled = f.rect
    f.update([person(905, 302, 1104, 801)], W, H, t + 1 / 30)   # a small shift: inside the dead zone
    assert f.rect == settled
    for _ in range(20):                                     # nobody, but for less than lost_s: hold
        t += 1 / 30
        f.update([], W, H, t)
    assert f.rect == settled
    for _ in range(150):                                    # gone for good: back to the full frame
        t += 1 / 30
        f.update([], W, H, t)
    assert f.rect[2] > W - 20


def test_apply_crops_and_scales_back():
    f = AutoFramer()
    img = np.zeros((H, W, 4), np.uint8)
    img[:, W // 2:] = 200                                   # right half bright
    assert f.apply(img, (0, 0, W, H)) is img                # full frame: untouched, no copy
    out = f.apply(img, (W / 2, 0, W / 2, H / 2))
    assert out.shape == img.shape and out[H // 2, W // 2, 0] == 200


def test_framing_makes_the_detector_needed():
    from vcstudio.ai.detection import DetectionConfig
    assert not DetectionConfig.from_profile({"rois": []}).detector_needed
    assert DetectionConfig.from_profile({"rois": [], "framing": {"enabled": True}}).detector_needed


def test_pipeline_outputs_the_framed_picture(paths, monkeypatch):
    from vcstudio.app.application import AppContext
    ctx = AppContext(paths, configure_logging=False)
    try:
        ctx.settings.set("input.method", "test_pattern")
        ctx.settings.set("output.camera_enabled", False)
        ctx.update_active_profile(lambda b: b.__setitem__("framing", {"enabled": True, "tau": 0.05}))
        assert ctx.pipeline.scene.framing.enabled
        # a person in the right half, small enough to zoom in on (a tall one would fill the frame: no crop)
        monkeypatch.setattr(ctx.ai, "tracked_at", lambda pts: [person(1500, 350, 1700, 700)])
        ctx.start_capture()
        end = time.monotonic() + 8
        out = None
        while time.monotonic() < end:
            pair, _ = ctx.pipeline.processed.peek()
            if pair is not None and pair[1].source.endswith("+framed") and ctx.pipeline.framer.rect[0] > 900:
                out = pair
                break
            time.sleep(0.05)
        assert out is not None, "no framed output"
        frame, framed = out
        with frame.bgra() as src, framed.bgra() as dst:
            assert not np.array_equal(src, dst)                     # the output is a crop, not the input
            assert dst.shape == src.shape                           # ...at the full output size
        ctx.update_active_profile(lambda b: b["framing"].__setitem__("enabled", False))
        end = time.monotonic() + 5
        while time.monotonic() < end and ctx.pipeline.framer.rect is not None:
            time.sleep(0.05)
        assert ctx.pipeline.framer.rect is None                     # switched off: reset
    finally:
        ctx.stop_capture()
        ctx.shutdown()


def test_preview_button(qtbot, paths):
    from vcstudio.app.application import AppContext
    from vcstudio.ui.main_window import MainWindow
    ctx = AppContext(paths, configure_logging=False)
    w = MainWindow(ctx)
    qtbot.addWidget(w)
    try:
        page = next(p for p in w.pages if p.key == "preview")
        page.framing_btn.setChecked(True)
        assert ctx.profiles.active()["body"]["framing"]["enabled"] is True
        page.framing_btn.setChecked(False)
        assert ctx.profiles.active()["body"]["framing"]["enabled"] is False
    finally:
        ctx.shutdown()


@pytest.mark.parametrize("zoom", [1.0, 4.0])
def test_zoom_limits(zoom):
    f = AutoFramer(FramingConfig(enabled=True, zoom_max=zoom))
    x, y, w, h = f.target([person(950, 500, 970, 540)], W, H)
    assert w >= W / zoom - 1e-6
