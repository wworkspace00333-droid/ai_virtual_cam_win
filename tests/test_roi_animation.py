"""0.7.0 keyframe animation of drawn regions: sampling, easing, loops, presets, the resolver and the compositor."""
from __future__ import annotations

import pytest

from vcstudio.roi import animation as an
from vcstudio.roi.model import ROI
from vcstudio.roi.resolver import resolve

BASE = {"cx": 0.5, "cy": 0.5, "w": 0.2, "h": 0.2, "rotation": 0.0, "opacity": 1.0}


def anim(**kw):
    d = {"enabled": True, "duration_s": 2.0, "loop": "loop", "easing": "linear",
         "keys": [{"at": 0.0, "cx": 0.2, "opacity": 1.0}, {"at": 1.0, "cx": 0.8, "opacity": 0.0}]}
    d.update(kw)
    return d


def test_linear_sampling_loop_and_pingpong():
    a = anim()
    assert an.sample(a, BASE, 0.0)["cx"] == pytest.approx(0.2)
    assert an.sample(a, BASE, 1.0)["cx"] == pytest.approx(0.5)
    assert an.sample(a, BASE, 1.0)["w"] == 0.2                         # not keyed: the drawn value
    assert an.sample(a, BASE, 2.5)["cx"] == pytest.approx(0.35)          # loops
    p = anim(loop="pingpong")
    assert an.sample(p, BASE, 3.0)["cx"] == pytest.approx(0.5)           # on the way back
    assert an.sample(p, BASE, 3.5)["cx"] == pytest.approx(0.35)


def test_easing_and_clamping():
    e = anim(easing="ease")
    assert an.sample(e, BASE, 0.5)["cx"] < an.sample(anim(), BASE, 0.5)["cx"]   # slow start
    assert an.sample(e, BASE, 1.0)["cx"] == pytest.approx(0.5)
    n = an.normalise({"enabled": True, "duration_s": -3, "loop": "x", "keys": [{"at": 5, "cx": 1}, {"at": -1}, "bad"]})
    assert n["duration_s"] == 0.1 and n["loop"] == "loop" and [k["at"] for k in n["keys"]] == [0.0, 1.0]
    assert not an.normalise({"enabled": True, "keys": [{"at": 0}]})["enabled"]     # one key: not animated
    assert an.sample({"enabled": False}, BASE, 1.0) == BASE


@pytest.mark.parametrize("name", an.PRESETS)
def test_presets_move_something(name):
    a = an.preset(name, BASE)
    s0, s1 = an.sample(a, BASE, 0.0), an.sample(a, BASE, 0.9)
    assert s0 != s1
    with pytest.raises(ValueError):
        an.preset("nope", BASE)


def test_resolver_animates_drawn_rectangles_only():
    r = ROI(name="A", cx=0.5, cy=0.5, w=0.2, h=0.2, animation=anim())
    auto = ROI(name="B", mode="auto", animation=anim())
    out = resolve([r, auto], [], 100, 100, t=1.0)
    assert len(out) == 1 and abs((out[0].bbox[0] + out[0].bbox[2]) / 2 - 50) < 1e-6 and out[0].opacity == 0.5
    still = resolve([r], [], 100, 100)                                   # no time: the drawn geometry
    assert still[0].opacity == 1.0 and still[0].bbox[0] == pytest.approx(40)
    assert resolve([r], [], 100, 100, t=1.999) == [] or resolve([r], [], 100, 100, t=1.999)[0].opacity < 0.01
    rr = ROI.from_dict(r.to_dict())
    assert rr.animation["keys"][1]["cx"] == 0.8                          # saved with the profile


def test_compositor_uses_the_animated_opacity(tmp_path):
    import numpy as np

    from tests.test_replacement import GREEN, H, W, frame, solid_png
    from vcstudio.replacement.assets import AssetLibrary
    from vcstudio.replacement.compositor import Compositor
    from vcstudio.replacement.spec import ReplacementSpec
    lib = AssetLibrary(tmp_path / "assets")
    a = lib.import_file(solid_png(tmp_path, (0, 0, 255, 255)))
    r = ROI(name="A", cx=0.5, cy=0.5, w=0.5, h=0.5,
            animation=anim(keys=[{"at": 0, "opacity": 0.5}, {"at": 1, "opacity": 0.5}]))
    reg = resolve([r], [], W, H, t=0.5)
    out = Compositor(lib).render(frame(), reg, {r.id: ReplacementSpec(enabled=True, mode="overlay", asset_id=a.id)},
                                 {a.id: a})
    assert np.allclose(out[H // 2, W // 2][:3], (20, 80, 148), atol=1)    # same as opacity 0.5 in the spec
    assert tuple(out[5, 5]) == GREEN
