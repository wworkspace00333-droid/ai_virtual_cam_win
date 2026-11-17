"""Lighting enhancement (roadmap P2.6): exposure metered on faces, grey-world white balance, smooth over time,
cheap enough per frame, applied before the background / replacements."""
from __future__ import annotations

import time

import numpy as np

from vcstudio.replacement.lighting import AutoLight, LightingConfig, luma

W, H = 1280, 720


def img(bgr, w=W, h=H):
    a = np.empty((h, w, 4), np.uint8)
    a[..., :3] = bgr
    a[..., 3] = 255
    return a


def mean_luma(a, box=None):
    if box is not None:
        x0, y0, x1, y1 = box
        a = a[y0:y1, x0:x1]
    return float(luma(a[..., :3].astype(np.float32)).mean())


def test_a_dim_picture_is_brightened_and_a_good_one_left_alone():
    al = AutoLight(LightingConfig(enabled=True, strength=1.0))
    dark = img((40, 40, 40))
    al.update(dark, [], 0.0)
    out = al.apply(dark)
    assert mean_luma(out) > 2 * mean_luma(dark) and out[..., 3].min() == 255      # alpha untouched
    ok = img((122, 122, 122))                                                       # 0.48 brightness, neutral grey
    al2 = AutoLight(LightingConfig(enabled=True, strength=1.0))
    al2.update(ok, [], 0.0)
    assert al2.apply(ok) is ok                                                      # nothing to do: no copy


def test_backlit_face_is_metered_on_the_face():
    """A dark face in front of a bright window: metering on the face brightens it much more than the frame mean."""
    a = img((235, 235, 235))                       # bright window
    face = (560, 260, 720, 460)
    a[face[1]:face[3], face[0]:face[2], :3] = (45, 55, 70)
    with_face, without = AutoLight(LightingConfig(enabled=True, strength=1.0)), AutoLight(
        LightingConfig(enabled=True, strength=1.0))
    with_face.update(a, [face], 0.0)
    without.update(a, [], 0.0)
    lit_face = mean_luma(with_face.apply(a), face)
    lit_plain = mean_luma(without.apply(a), face)
    assert lit_face > lit_plain + 0.1 and lit_face > 0.3
    assert lit_face > mean_luma(a, face) * 1.5             # the face really got brighter (it did not, with padding)


def test_highlights_roll_off_instead_of_clipping():
    al = AutoLight(LightingConfig(enabled=True, strength=1.0))
    al.gains = np.array([2.0, 2.0, 2.0], np.float32)
    lut = al.lut()[:, 0, 0].astype(int)
    assert lut[200] < 255 and lut[230] > lut[200] and lut[255] <= 255             # still increasing near the top
    assert np.all(np.diff(lut) >= 0)                                                # monotonic


def test_white_balance_removes_a_colour_cast():
    blue = img((170, 120, 90))                     # strong blue cast (BGR)
    al = AutoLight(LightingConfig(enabled=True, strength=1.0))
    al.update(blue, [], 0.0)
    out = al.apply(blue)[..., :3].reshape(-1, 3).mean(axis=0)
    spread_before = 170 - 90
    assert out.max() - out.min() < spread_before * 0.6
    nowb = AutoLight(LightingConfig(enabled=True, strength=1.0, white_balance=False))
    nowb.update(blue, [], 0.0)
    g = nowb.gains
    assert abs(g[0] - g[2]) < 1e-6                                                  # exposure only: no tint change


def test_changes_are_smooth_over_time():
    al = AutoLight(LightingConfig(enabled=True, strength=1.0, tau=0.8))
    al.update(img((120, 120, 120)), [], 0.0)
    g0 = float(al.gains[1])
    al.update(img((30, 30, 30)), [], 1 / 30)       # the room suddenly goes dark
    g1 = float(al.gains[1])
    assert g0 < g1 < g0 + 0.2                      # one frame later: only a small step
    t = 1 / 30
    for _ in range(120):
        t += 1 / 30
        al.update(img((30, 30, 30)), [], t)
    assert float(al.gains[1]) > 1.9                # after 4 s: fully adapted (clamped at 2.2)


def test_cost_per_1080p_frame():
    al = AutoLight(LightingConfig(enabled=True))
    a = img((60, 70, 80), 1920, 1080)
    al.update(a, [], 0.0)
    al.apply(a)
    times = []
    for i in range(10):
        t0 = time.perf_counter()
        al.update(a, [(800, 300, 1000, 550)], i / 30)
        al.apply(a)
        times.append((time.perf_counter() - t0) * 1000)
    # the fastest frame: what the code costs without a busy runner's interruptions. Was ~41 ms on a GitHub runner
    # (two full-frame copies for a 3-channel slice); with the 4-channel table ~6 ms on the 2-core server
    assert min(times) < 20, times


def test_pipeline_lights_the_camera_picture(paths, monkeypatch):
    from vcstudio.app.application import AppContext
    ctx = AppContext(paths, configure_logging=False)
    try:
        ctx.settings.set("input.method", "test_pattern")
        ctx.settings.set("output.camera_enabled", False)
        ctx.update_active_profile(lambda b: b.__setitem__("lighting", {"enabled": True, "strength": 1.0}))
        assert ctx.pipeline.scene.lighting.enabled
        ctx.start_capture()
        end = time.monotonic() + 8
        while time.monotonic() < end and ctx.pipeline.light._t is None:
            time.sleep(0.05)
        assert ctx.pipeline.light._t is not None and ctx.pipeline.stats().last_error == ""
        ctx.update_active_profile(lambda b: b["lighting"].__setitem__("enabled", False))
        end = time.monotonic() + 5
        while time.monotonic() < end and ctx.pipeline.light._t is not None:
            time.sleep(0.05)
        assert ctx.pipeline.light._t is None                                       # off: reset
    finally:
        ctx.stop_capture()
        ctx.shutdown()


def test_preview_button_turns_on_face_metering(qtbot, paths):
    from vcstudio.app.application import AppContext
    from vcstudio.ui.main_window import MainWindow
    ctx = AppContext(paths, configure_logging=False)
    w = MainWindow(ctx)
    qtbot.addWidget(w)
    try:
        page = next(p for p in w.pages if p.key == "preview")
        page.light_btn.setChecked(True)
        body = ctx.profiles.active()["body"]
        assert body["lighting"]["enabled"] is True and body["ai"]["face_detection"] == "vision"
    finally:
        ctx.shutdown()
