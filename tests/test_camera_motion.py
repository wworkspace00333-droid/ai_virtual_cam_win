"""AI handheld camera motion (0.7.0): organic, non-repeating, rigid movement of the whole picture."""
from __future__ import annotations

from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from vcstudio.replacement import camera_motion as cm
from tests.test_scenes import ctx, wait_for  # noqa: F401  (fixture)


def cfg(**kw) -> cm.MotionConfig:
    return cm.MotionConfig.from_dict({"enabled": True, "auto_intensity": False, **kw})


def series(c: cm.MotionConfig, seconds=60.0, fps=30.0, t0=50.0):
    poses = cm.simulate(c, seconds, fps, t0)
    return np.array([[p.tx, p.ty, p.rot, p.zoom] for p in poses])


def test_noise_is_smooth_bounded_and_seeded():
    xs = np.linspace(0, 200, 20001)
    v = np.array([cm.perlin(x, 3) for x in xs])
    assert np.abs(v).max() <= 1.0 and v.std() > 0.15
    assert np.abs(np.diff(v)).max() < 0.05                         # continuous: no jumps between close samples
    assert cm.perlin(12.3, 3) == cm.perlin(12.3, 3)
    assert cm.perlin(12.3, 3) != cm.perlin(12.3, 4)
    assert cm.perlin(7.0, 1) == pytest.approx(0.0, abs=1e-12)        # gradient noise is zero on the lattice


def test_config_is_clamped():
    c = cm.MotionConfig.from_dict({"intensity": 5, "speed": 0, "mode": "wild", "quality": "x", "seed": "a",
                                   "frequency": 99})
    assert c.intensity == 1.0 and c.speed == 0.1 and c.mode == "natural" and c.quality == "auto"
    assert c.seed == 1 and c.frequency == 15.0 and not c.enabled
    assert set(cm.PRESETS) == set(cm.PRESET_LABELS) and set(cm.MODE_SHAPE) == set(cm.MODES)
    for p in cm.PRESETS.values():
        assert cm.MotionConfig.from_dict(p).preset_values()["mode"] == p["mode"]


@pytest.mark.parametrize("mode", cm.MODES)
def test_motion_is_smooth_small_and_not_white_noise(mode):
    s = series(cfg(mode=mode, intensity=1.0, horizontal=1, vertical=1, rotation=1, zoom=1))
    tx, ty, rot = s[:, 0], s[:, 1], s[:, 2]
    assert np.abs(tx).max() <= cm.MAX_SHIFT * 1.7 and np.abs(rot).max() <= cm.MAX_ROT_DEG * 1.7
    assert tx.std() > cm.MAX_SHIFT * 0.05                           # it does move
    for ch in (tx, ty):
        d = np.diff(ch)
        assert np.abs(d).max() < cm.MAX_SHIFT * 0.25                  # never a jump between two frames
        a = ch - ch.mean()
        assert (a[1:] * a[:-1]).sum() / (a * a).sum() > 0.9          # organic, frame-to-frame correlated


def test_no_short_loop():
    """No lag between 1 and 30 s makes the path repeat itself (loop prevention)."""
    s = series(cfg(mode="natural"), seconds=90.0, fps=20.0)
    x = s[:, 0] - s[:, 0].mean()
    best = max(float(np.corrcoef(x[:-lag], x[lag:])[0, 1]) for lag in range(20, 600, 5))
    assert best < 0.9, best
    p = series(cfg(mode="product"), seconds=90.0, fps=20.0)[:, 0]     # the arc is periodic, the noise on it is not
    assert np.abs(p[:-286] - p[286:]).max() > 1e-5


def test_intensity_seed_and_stabilization():
    assert np.abs(series(cfg(intensity=0.0))).max() == 0.0
    a, b = series(cfg(seed=1)), series(cfg(seed=2))
    assert np.abs(a - b).max() > cm.MAX_SHIFT * 0.05                 # another character
    loud = np.diff(series(cfg(stabilization=0.0, smoothness=0.0, frequency=10))[:, 0]).std()
    calm = np.diff(series(cfg(stabilization=1.0, smoothness=0.0, frequency=10))[:, 0]).std()
    assert calm < loud * 0.6                                          # the tremor is mostly gone


def test_matrix_is_rigid_and_covers_the_output():
    w, h = 640, 360
    img = np.full((h, w, 4), 200, np.uint8)
    rng = np.random.default_rng(0)
    for _ in range(200):
        pose = cm.Pose(*rng.uniform(-1, 1, 4) * [0.04, 0.04, 2.5, 0.03])
        pivot = (rng.uniform(80, w - 80), rng.uniform(60, h - 60))
        m, s = cm.matrix(w, h, pose, pivot, 1.0)
        sv = np.linalg.svd(m[:, :2], compute_uv=False)
        assert sv[0] == pytest.approx(sv[1], rel=1e-9)                # uniform scale + rotation: no stretch, no shear
        out = cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        assert out[..., 0].min() == 200                               # no border pixel anywhere


def test_object_stays_away_from_the_edges_and_is_the_pivot():
    w, h = 640, 360
    img = np.zeros((h, w, 4), np.uint8)
    img[..., 3] = 255
    box = (480.0, 120.0, 595.0, 240.0)                                # close to the right edge
    obj = SimpleNamespace(label="handbag", x0=box[0], y0=box[1], x1=box[2], y1=box[3])
    person = SimpleNamespace(label="person", x0=0.0, y0=0.0, x1=400.0, y1=360.0)
    eng = cm.HandheldMotion()
    c = cfg(mode="documentary", intensity=1.0, horizontal=1, vertical=1, rotation=1, object_aware=True)
    pts = np.array([[box[0], box[1], 1], [box[2], box[1], 1], [box[0], box[3], 1], [box[2], box[3], 1]])
    piv = np.array([(box[0] + box[2]) / 2, (box[1] + box[3]) / 2, 1.0])
    for i in range(240):
        t = 10 + i / 30
        eng.process(img, c, t, [person, obj])
        if i < 15:
            continue
        assert eng.stats.target == "handbag"                          # things before people
        q = pts @ eng.last_matrix.T                                    # the transform actually used
        assert q[:, 0].max() <= w - cm.EDGE_MARGIN * w + 1 and q[:, 0].min() >= cm.EDGE_MARGIN * w - 1
        assert q[:, 1].max() <= h - cm.EDGE_MARGIN * h + 1 and q[:, 1].min() >= cm.EDGE_MARGIN * h - 1
        moved = piv @ eng.last_matrix.T - piv[:2]                      # roll and zoom turn around the object
        assert np.abs(moved).max() <= cm.MAX_SHIFT * 1.7 * w
    k = cm.HandheldMotion._keep_inside(lambda k: cm.matrix(w, h, cm.Pose(0.05 * k, 0, 0, 0), (w / 2, h / 2), 1.0)[0],
                                       box, w, h)
    assert 0.0 < k < 1.0                                               # a 5 % push right is scaled down


def test_switching_on_and_off_eases():
    img = np.random.default_rng(1).integers(0, 255, (180, 320, 4), np.uint8)
    eng = cm.HandheldMotion()
    off, on = cfg(enabled=False), cfg(intensity=1.0)
    assert eng.process(img, off, 1.0) is img and not eng.active
    levels = []
    for i in range(1, 30):
        out = eng.process(img, on, 1.0 + i / 30)
        levels.append(eng.level)
        assert out is not img and out.shape == img.shape
    assert levels[0] < 0.2 and levels[-1] == 1.0                      # eased in over FADE_S, not switched hard
    for i in range(30, 60):
        out = eng.process(img, off, 1.0 + i / 30)
    assert not eng.active and out is img                              # eased out, then the frame is untouched


def test_auto_intensity_backs_off_when_the_picture_moves():
    rng = np.random.default_rng(2)
    still = rng.integers(0, 255, (360, 640, 4), np.uint8)
    eng = cm.HandheldMotion()
    c = cfg(auto_intensity=True)
    for i in range(60):
        eng.process(still, c, i / 30)
    assert eng.stats.auto_factor > 0.95
    for i in range(60, 200):
        eng.process(rng.integers(0, 255, (360, 640, 4), np.uint8), c, i / 30)
    assert eng.stats.auto_factor < 0.5


def test_pipeline_applies_and_removes_the_motion(ctx):  # noqa: F811
    ctx.start_capture()
    assert wait_for(lambda: ctx.pipeline.stats().frames_out > 5)
    ctx.update_active_profile(lambda b: b.__setitem__("camera_motion", {**cm.PRESETS["natural_handheld"],
                                                                        "enabled": True}))
    assert ctx.pipeline.scene.motion.enabled
    assert wait_for(lambda: ctx.pipeline.motion.level >= 1.0 and ctx.pipeline.motion.stats.ms > 0)
    pair, _ = ctx.pipeline.processed.peek()
    assert pair[1].source.endswith("+motion")
    assert ctx.pipeline.motion.stats.backend in ("CPU", "OpenCL")
    ctx.update_active_profile(lambda b: b["camera_motion"].__setitem__("enabled", False))
    assert wait_for(lambda: not ctx.pipeline.motion.active)
    assert wait_for(lambda: "+motion" not in ctx.pipeline.processed.peek()[0][1].source)


def test_motion_tab(qtbot, ctx):  # noqa: F811
    from vcstudio.ui.main_window import MainWindow
    w = MainWindow(ctx)
    qtbot.addWidget(w)
    studio = next(p for p in w.pages if p.key == "studio")
    tab = studio.part("AI Camera Motion")
    assert tab is not None
    body = lambda: ctx.profiles.active()["body"].get("camera_motion") or {}  # noqa: E731
    tab.enabled.setChecked(True)
    assert body()["enabled"] is True and ctx.pipeline.scene.motion.enabled
    tab.preset.setCurrentIndex(tab.preset.findData("product_video"))
    assert body()["mode"] == "product" and body()["intensity"] == 0.5
    assert tab.mode.currentData() == "product" and tab.preset.currentData() == "product_video"
    tab.sliders["intensity"].setValue(80)
    assert body()["intensity"] == 0.8 and tab.preset.currentData() == ""
    assert tab.values["intensity"].text() == "80 %"
    tab.save_preset("Bags")
    assert ctx.settings.get("motion_presets")["Bags"]["intensity"] == 0.8
    tab.reset()
    assert body()["intensity"] == cm.MotionConfig().intensity and body()["enabled"] is True
    tab.apply_preset("Bags")
    assert body()["intensity"] == 0.8 and body()["mode"] == "product"
    tab.delete_preset("Bags")
    assert "Bags" not in ctx.settings.get("motion_presets")
    seed = body()["seed"]
    tab.new_seed.click()
    assert body()["seed"] != seed
    assert not tab.path.pixmap().isNull()
    tab.tick()
    w.on_action("motion_toggle")                                       # the hotkey path
    assert body()["enabled"] is False and not tab.enabled.isChecked()
    w.on_action("motion_toggle")
    assert body()["enabled"] is True and tab.enabled.isChecked()
