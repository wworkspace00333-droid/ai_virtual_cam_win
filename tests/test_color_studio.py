"""0.6.0 Phase 6: colour controls, .cube LUTs (1D / 3D), import, per-scene application without double grading."""
from __future__ import annotations

import time

import numpy as np
import pytest

from vcstudio.replacement import color as cs


def cube3(path, fn, n=17, extra=""):
    lines = ["TITLE \"t\"", f"LUT_3D_SIZE {n}", extra]
    for b in range(n):
        for g in range(n):
            for r in range(n):
                lines.append("%.6f %.6f %.6f" % fn(r / (n - 1), g / (n - 1), b / (n - 1)))
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def img(w=96, h=54):
    rng = np.random.default_rng(3)
    a = rng.integers(0, 256, (h, w, 4), dtype=np.uint8)
    a[..., 3] = 200
    return a


def test_config_validation_and_neutral():
    c = cs.ColorConfig.from_profile({"color": {"enabled": True, "exposure": 9, "gamma": "x", "lut": None}})
    assert c.exposure == 2.0 and c.gamma == 1.0 and c.lut == ""
    assert not cs.ColorConfig(enabled=True).active and not cs.ColorConfig(exposure=1).active
    assert cs.ColorConfig(enabled=True, vignette=0.2).active
    a = img()
    assert cs.ColorGrader().apply(a, cs.ColorConfig(enabled=True)) is a       # neutral costs nothing


def test_tone_controls_move_values_the_right_way():
    g = cs.ColorGrader()
    grey = np.full((8, 8, 4), 128, np.uint8)
    up = g.apply(grey, cs.ColorConfig(enabled=True, exposure=1.0))
    assert up[0, 0, 0] > 200 and up[0, 0, 3] == 128                          # brighter, alpha untouched
    warm = g.apply(grey, cs.ColorConfig(enabled=True, temperature=1.0))
    assert warm[0, 0, 2] > 128 > warm[0, 0, 0]                                # more red, less blue
    flat = g.apply(img(), cs.ColorConfig(enabled=True, contrast=0.0))
    assert flat[..., :3].std() < 2                                            # zero contrast: one grey
    t = cs.tone_tables(1.0, 0, 1, 1, 0, -1.0, 0, 0)[:, 0, 0]
    assert t[200] < 255 and np.all(np.diff(t.astype(int)) >= 0)               # tone mapping keeps highlight detail


def test_saturation_and_skin_protection():
    g = cs.ColorGrader()
    a = img()
    grey = g.apply(a, cs.ColorConfig(enabled=True, saturation=0.0))
    assert np.abs(grey[..., 0].astype(int) - grey[..., 2]).max() <= 1
    skin = np.zeros((40, 40, 4), np.uint8)
    skin[..., :3] = (120, 150, 210)                                            # a skin tone (BGR)
    skin[..., 3] = 255
    plain = g.apply(skin, cs.ColorConfig(enabled=True, saturation=0.0))
    kept = g.apply(skin, cs.ColorConfig(enabled=True, saturation=0.0, skin_protect=True))
    spread = lambda x: int(x[20, 20, 2]) - int(x[20, 20, 0])                  # noqa: E731
    assert spread(kept) > spread(plain) + 40                                   # skin keeps most of its colour


def test_parse_cube_and_errors(tmp_path):
    p = cube3(tmp_path / "id.cube", lambda r, g, b: (r, g, b), extra="DOMAIN_MIN 0 0 0")
    kind, data, dom = cs.parse_cube(p.read_text())
    assert kind == "3d" and data.shape == (17, 17, 17, 3) and dom == (0.0, 1.0)
    assert np.allclose(data[0, 0, 16], (1, 0, 0))                              # red runs fastest
    one = cs.parse_cube("LUT_1D_SIZE 3\n0 0 0\n0.5 0.5 0.5\n1 1 1\n")
    assert one[0] == "1d" and one[1].shape == (3, 3)
    for bad in ("LUT_3D_SIZE 2\n0 0 0\n", "LUT_3D_SIZE 2\n" + "0 0\n" * 8, "HELLO\n", "0 0 0\n",
                "LUT_1D_SIZE 2\nDOMAIN_MIN 1 1 1\nDOMAIN_MAX 0 0 0\n0 0 0\n1 1 1\n"):
        with pytest.raises(cs.LutError):
            cs.parse_cube(bad)


def test_identity_3d_lut_keeps_the_picture_and_an_invert_lut_inverts(tmp_path):
    g = cs.ColorGrader()
    a = img(120, 68)
    ident = cube3(tmp_path / "id.cube", lambda r, g_, b: (r, g_, b))
    out = g.apply(a, cs.ColorConfig(enabled=True, lut=str(ident)))
    assert np.abs(out[..., :3].astype(int) - a[..., :3]).mean() < 3           # 6-bit grid + half-size delta
    assert np.array_equal(out[..., 3], a[..., 3])
    inv = cube3(tmp_path / "inv.cube", lambda r, g_, b: (1 - r, 1 - g_, 1 - b))
    flat = np.full((60, 80, 4), 40, np.uint8)
    out = g.apply(flat, cs.ColorConfig(enabled=True, lut=str(inv)))
    assert abs(int(out[30, 40, 0]) - 215) <= 4                                # 255 - 40, within the grid step
    half = g.apply(flat, cs.ColorConfig(enabled=True, lut=str(inv), lut_intensity=0.5))
    assert abs(int(half[30, 40, 0]) - 128) <= 4
    one = tmp_path / "one.cube"
    one.write_text("LUT_1D_SIZE 2\n1 1 1\n0 0 0\n")                           # 1D invert, exact
    assert g.apply(flat, cs.ColorConfig(enabled=True, lut=str(one)))[0, 0, 0] == 215
    g.apply(flat, cs.ColorConfig(enabled=True, lut=str(tmp_path / "missing.cube")))
    assert "missing" in g.error or g.error                                     # a missing LUT: picture unchanged


def test_effects_change_the_picture_where_expected():
    g = cs.ColorGrader()
    white = np.full((90, 160, 4), 200, np.uint8)
    v = g.apply(white, cs.ColorConfig(enabled=True, vignette=1.0))
    assert v[45, 80, 0] >= 198 and v[2, 2, 0] < 120                           # dark corners, centre kept
    n1 = g.apply(white, cs.ColorConfig(enabled=True, grain=1.0), t=0.0)
    n2 = g.apply(white, cs.ColorConfig(enabled=True, grain=1.0), t=0.5)
    assert n1[..., 0].std() > 3 and not np.array_equal(n1, n2)                # grain moves between frames
    edge = np.zeros((40, 40, 4), np.uint8)
    edge[:, 20:] = 160
    s = g.apply(edge, cs.ColorConfig(enabled=True, sharpness=1.0))
    assert s[20, 21, 0] > 160                                                  # overshoot at the edge


def test_import_lut_validates_and_copies(tmp_path):
    src = cube3(tmp_path / "My Look.cube", lambda r, g, b: (r, g, b), n=5)
    dest = cs.import_lut(src, tmp_path / "luts")
    assert dest.name == "My-Look.cube" and dest.exists()
    assert cs.import_lut(src, tmp_path / "luts").name == "My-Look-2.cube"
    bad = tmp_path / "bad.cube"
    bad.write_text("nonsense")
    with pytest.raises(cs.LutError):
        cs.import_lut(bad, tmp_path / "luts")
    with pytest.raises(cs.LutError):
        cs.import_lut(tmp_path / "x.png", tmp_path / "luts")


def test_pipeline_grades_once_per_frame_and_follows_the_scene(paths):
    from vcstudio.app.application import AppContext
    ctx = AppContext(paths, configure_logging=False)
    try:
        ctx.settings.set("output.camera_enabled", False)
        ctx.settings.set("input.method", "test_pattern")
        ctx.settings.set("input.preferred_width", 640)
        ctx.settings.set("input.preferred_height", 360)
        ctx.update_active_profile(lambda b: b.__setitem__("color", {"enabled": True, "exposure": 1.0}))
        ctx.profiles.create("Plain")
        ctx.start_capture()

        lut = cs.tone_tables(1.0, 0, 1, 1, 0, 0, 0, 0)[:, 0, 0]

        def graded(pair) -> bool | None:
            """True: output = input graded exactly once; False: output = input; None: neither (in between)."""
            with pair[0].bgra() as i, pair[1].bgra() as o:
                iv, ov = i[::7, ::7, 0].astype(int), o[::7, ::7, 0].astype(int)
                if np.array_equal(iv, ov):
                    return False
                return True if np.abs(lut[iv] - ov).max() <= 1 else None

        def wait(want: bool):
            deadline = time.time() + 15
            while time.time() < deadline:
                pair, _ = ctx.pipeline.processed.peek()
                if pair is not None and graded(pair) is want:
                    return True
                time.sleep(0.05)
            return False
        assert wait(True)                                                       # graded once, not twice
        ctx.scenes.switch("Plain")                                             # a scene without grading
        assert wait(False)
    finally:
        ctx.stop_capture()
        ctx.shutdown()


def test_frame_rate_guard_reduces_expensive_effects_and_retries():
    g = cs.ColorGrader()
    a = np.full((360, 640, 4), 120, np.uint8)
    cfg = cs.ColorConfig(enabled=True, exposure=0.2, glow=0.5, grain=0.5, sharpness=0.5)
    for i in range(cs.OVER_FRAMES):
        g.apply(a, cfg, i / 30, budget_ms=0.0001)                 # an impossible budget: always over
    assert g.reduced
    r1 = g.apply(a, cfg, 0.0, budget_ms=0.0001)
    r2 = g.apply(a, cfg, 0.5, budget_ms=0.0001)
    assert np.array_equal(r1, r2)                                # no grain any more (it would move with t)
    for i in range(cs.RETRY_FRAMES):
        g.apply(a, cfg, i, budget_ms=0.0001)
    assert not g.reduced                                         # tried again
    g2 = cs.ColorGrader()
    for i in range(50):
        g2.apply(a, cfg, i, budget_ms=1e9)                       # within budget: never reduced
    assert not g2.reduced


def _d3d11_lut():
    import sys
    if sys.platform != "win32":
        return None
    from vcstudio.platform.win import d3d11
    if not d3d11.available():
        return None
    ctx = d3d11.D3D11Context()
    return ctx if ctx.has_lut else None


def test_gpu_lut_matches_exact_trilinear_better_than_the_cpu_path(tmp_path):
    ctx = _d3d11_lut()
    if ctx is None:
        pytest.skip("vcs_d3d11.dll with the LUT pass not built")
    p = cube3(tmp_path / "look.cube", lambda r, g, b: (r ** 0.8, g, b ** 1.2), n=17)
    kind, tab = cs.load_lut(str(p), p.stat().st_mtime)
    ctx.upload_lut(tab, cs.LUT_GRID)
    a = np.random.default_rng(5).integers(0, 256, (90, 160, 4), dtype=np.uint8)
    out = ctx.apply_lut(a, 1.0)
    _, data, _ = cs.parse_cube(p.read_text())
    ref = (cs._trilinear(data, a[..., [2, 1, 0]].reshape(-1, 3) / 255.0) * 255).reshape(90, 160, 3)[..., ::-1]
    gpu_err = np.abs(out[..., :3].astype(float) - ref).mean()
    cpu = cs.ColorGrader()
    cpu.gpu_mode = "off"
    cpu_err = np.abs(cpu.apply(a, cs.ColorConfig(enabled=True, lut=str(p)))[..., :3].astype(float) - ref).mean()
    assert gpu_err < 1.0 and gpu_err < cpu_err and np.array_equal(out[..., 3], a[..., 3])
    half = ctx.apply_lut(a, 0.0)
    assert np.abs(half.astype(int) - a.astype(int)).max() <= 1          # intensity 0: the input
    ctx.close()


def test_grader_gpu_modes_and_fallback(tmp_path):
    p = cube3(tmp_path / "id.cube", lambda r, g, b: (r, g, b), n=9)
    a = np.full((40, 60, 4), 100, np.uint8)
    cfg = cs.ColorConfig(enabled=True, lut=str(p))
    g = cs.ColorGrader()                                  # auto: WARP is skipped, hardware would be used
    g.apply(a, cfg)
    if _d3d11_lut() is not None:
        forced = cs.ColorGrader()
        forced.gpu_mode = "on"
        out = forced.apply(a, cfg)
        assert forced.gpu_used and abs(int(out[20, 30, 0]) - 100) <= 1

        class Broken:
            has_lut, software = True, False

            def upload_lut(self, *a):
                raise RuntimeError("device lost")

            def close(self):
                pass
        bad = cs.ColorGrader()
        bad._gpu = Broken()
        out = bad.apply(a, cfg)                          # GPU error: CPU result, GPU off from now on
        assert not bad.gpu_used and bad._gpu_failed and abs(int(out[20, 30, 0]) - 100) <= 3
    off = cs.ColorGrader()
    off.gpu_mode = "off"
    off.apply(a, cfg)
    assert not off.gpu_used
