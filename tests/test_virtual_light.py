"""0.7.0 virtual studio lights: light maps, colour temperature, person-only lighting and the pipeline stage."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace as NS

import numpy as np

from vcstudio.core.frame import Frame
from vcstudio.replacement import virtual_light as vl
from vcstudio.replacement.lighting import LightingConfig

W, H = 640, 360
FACE = (270.0, 80.0, 370.0, 200.0)


def grey():
    a = np.full((H, W, 4), 110, np.uint8)
    a[..., 3] = 255
    return a


def test_colour_temperature():
    d = vl.kelvin_bgr(6500)
    assert np.allclose(d, 1.0, atol=0.02)
    warm, cool = vl.kelvin_bgr(3000), vl.kelvin_bgr(7500)
    assert warm[2] > warm[0] and cool[0] > cool[2]                       # warm = more red, cool = more blue


def test_key_side_is_brighter_and_rim_sits_on_the_outline():
    person = vl.person_ellipse(W, H, FACE)
    gain, rim = vl.light_maps(W, H, FACE, person, vl.LightRig(key=0.6, key_angle=-60, fill=0.0, rim=0.5))
    y = 140
    left, right = gain[y, 285], gain[y, 355]
    assert left > right + 0.05                                            # key from the picture's left
    assert gain[10, 10] == 0 and rim[10, 10] == 0                         # nothing outside the person
    ys, xs = np.nonzero(rim > 0.05)
    assert len(xs) and np.mean(xs) > 320                                  # rim on the far (right) side
    assert (person[ys, xs] < 0.99).mean() > 0.5                           # ... near the outline, not the middle
    g2, _ = vl.light_maps(W, H, FACE, person, vl.LightRig(key=0.6, key_angle=60, fill=0.0, rim=0.0))
    assert g2[y, 355] > g2[y, 285] + 0.05                                 # from the right


def test_fill_lifts_the_shadow_side():
    person = vl.person_ellipse(W, H, FACE)
    no_fill, _ = vl.light_maps(W, H, FACE, person, vl.LightRig(key=0.6, key_angle=-60, fill=0.0, rim=0))
    fill, _ = vl.light_maps(W, H, FACE, person, vl.LightRig(key=0.6, key_angle=-60, fill=1.0, rim=0))
    assert fill[140, 355] > no_fill[140, 355] + 0.05


def test_relight_touches_only_the_person_mask_and_costs():
    img = grey()
    mask = np.zeros((90, 160), np.float32)
    mask[15:90, 60:100] = 1.0                                             # the person, segmentation-style
    rig = vl.LightRig(key=0.6, key_angle=-40, fill=0.2, rim=0.4, key_temp=3200)
    out = vl.relight(img, FACE, rig, mask)
    assert out is not img and int(out[140, 300, 2]) > 115                 # lit, warm: red raised
    assert np.array_equal(out[:, :150], img[:, :150])                     # background untouched
    assert vl.relight(img, None, rig) is img and vl.relight(img, FACE, vl.LightRig(0, 0, 5600, 0, 0)) is img
    big = np.full((1080, 1920, 4), 110, np.uint8)
    face = tuple(v * 3 for v in FACE)
    vl.relight(big, face, rig)
    t = time.perf_counter()
    for _ in range(5):
        vl.relight(big, face, rig)
    ms = (time.perf_counter() - t) * 200
    print(f"virtual lights, 1080p: {ms:.1f} ms")
    assert ms < 120


def test_config_presets_and_pipeline_stage():
    c = LightingConfig.from_profile({"lighting": {"vl_enabled": True, "vl_key": 7, "vl_key_angle": -200,
                                                  "vl_key_temp": 100}})
    assert c.vl_enabled and c.vl_key == 1 and c.vl_key_angle == -90 and c.vl_key_temp == 2700
    for p in vl.PRESETS.values():
        assert LightingConfig.from_profile({"lighting": p}).rig().active
    from vcstudio.core.video_pipeline import VideoPipeline
    me = NS(_lock=threading.Lock(), _stats=NS(last_error=""), vision=None, background=None, relit=False,
            ai=NS(tracked_at=lambda t: [NS(label="face", x0=FACE[0], y0=FACE[1], x1=FACE[2], y1=FACE[3])]))
    fr = Frame(1, 1.0, 1.0, W, H, array=grey(), source="cam")
    out = VideoPipeline._relight(me, fr, c, fr)
    assert out.source == "cam+lights" and me.relit
    me.ai = NS(tracked_at=lambda t: [])
    assert VideoPipeline._relight(me, fr, c, fr) is fr and not me.relit   # no face: unchanged
