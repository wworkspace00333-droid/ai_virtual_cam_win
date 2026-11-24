"""Custom background models of other layouts: 1-channel person maps (matting / selfie segmentation) and semantic
class maps (Pascal VOC / Cityscapes / ADE20K). Real ONNX Runtime on tiny hand-written models (tests/onnx_tiny.py):
the map is a 1x1 convolution of the colour, so a red patch is "the person" and the expected mask is known."""
from __future__ import annotations

from types import SimpleNamespace as NS

import numpy as np
import pytest

from tests.onnx_tiny import conv1x1
from vcstudio.ai import model_check as mc
from vcstudio.ai import person_segmentation as ps


@pytest.mark.parametrize("shape, kind, last, channel", [
    ([1, 2, 192, 192], "two_class", False, 1),          # PP-HumanSeg
    ([1, 1, 512, 512], "matte", False, 0),              # MODNet / RVM alpha
    ([1, 256, 256, 1], "matte", True, 0),               # MediaPipe selfie segmentation
    ([1, 144, 256], "matte", False, 0),
    ([1, 21, 513, 513], "semantic", False, 15),         # DeepLab Pascal VOC
    ([1, 513, 513, 21], "semantic", True, 15),
    ([1, 19, 512, 1024], "semantic", False, 11),        # Cityscapes
    ([1, 150, 128, 128], "semantic", False, 12),        # ADE20K
    ([1, 256, 256, 2], "two_class", True, 1),
])
def test_layouts_are_read_from_the_output_shape(shape, kind, last, channel):
    lay = ps.seg_layout(shape)
    assert lay is not None and (lay.kind, lay.channels_last, lay.channel) == (kind, last, channel)


@pytest.mark.parametrize("shape", [[1, 7, 64, 64], [1, 3, 8, 8], [2, 1, 64, 64], [1, 84, 8400], [1, 64, 64, 80]])
def test_other_maps_are_not_person_layouts(shape):
    assert ps.seg_layout(shape) is None


def test_person_map_decodes_every_layout():
    logits = np.full((1, 1, 20, 20), -4.0, np.float32)
    logits[0, 0, 5:10, 5:10] = 4.0
    m = ps.person_map(logits, ps.seg_layout(logits.shape))
    assert m.shape == (20, 20) and m[7, 7] > 0.98 and m[0, 0] < 0.02          # logits -> sigmoid
    prob = np.clip(logits, 0, 1) * 0.8
    assert np.allclose(ps.person_map(prob, ps.seg_layout(prob.shape)), prob[0, 0])   # 0..1 kept as it is
    voc = np.zeros((1, 20, 20, 21), np.float32)
    voc[0, 5:10, 5:10, 15] = 9.0
    v = ps.person_map(voc, ps.seg_layout(voc.shape))
    assert v[7, 7] > 0.99 and abs(v[0, 0] - 1 / 21) < 1e-4                  # softmax over 21 classes
    two = np.stack([np.zeros((20, 20)), np.where(m > 0.5, 6.0, -6.0)])[None].astype(np.float32)
    t = ps.person_map(two, ps.seg_layout(two.shape))
    assert t[7, 7] > 0.99 and t[0, 0] < 0.01


def _red_scene(h=120, w=160):
    img = np.full((h, w, 4), 128, np.uint8)
    img[30:90, 50:110, :3] = (0, 0, 255)                                     # BGR red
    return img


def _matte_model(path, side=64):
    # NCHW input scaled to -1..1: red (1, -1, -1) -> 4*1 + 2*1 + 2*1 - 2 = 6; grey (0, 0, 0) -> -2 (logits)
    return conv1x1(path, np.array([[4.0, -2.0, -2.0]], np.float32), np.array([-2.0]), side=side)


def _voc_model(path, side=48):
    # NHWC input 0..1: class 15 (person) = 10 R - 5 G - 5 B; every other class 0
    w = np.zeros((21, 3), np.float32)
    w[15] = (10.0, -5.0, -5.0)
    return conv1x1(path, w, np.zeros(21), side=side, nhwc=True, out_nhwc=True)


@pytest.mark.parametrize("make, kind", [(_matte_model, "matte"), (_voc_model, "semantic")])
def test_segmenter_runs_custom_layouts(tmp_path, make, kind):
    seg = ps.PersonSegmenter(path=make(tmp_path / "m.onnx"), compute_units="cpu_only")
    assert seg.layout.kind == kind
    p = seg.probability(_red_scene())
    h, w = p.shape
    inside = p[int(h * 0.35):int(h * 0.65), int(w * 0.38):int(w * 0.62)]
    assert inside.min() > 0.9 and p[: h // 8].max() < 0.2


def test_dynamic_size_runs_at_the_checked_size(tmp_path):
    seg = ps.PersonSegmenter(path=conv1x1(tmp_path / "d.onnx", np.array([[4.0, -2, -2], [-4.0, 2, 2]], np.float32),
                                          np.array([-2.0, 2.0]), side="S"), compute_units="cpu_only")
    assert (seg.h, seg.w, seg.layout.kind) == (ps.DYNAMIC_SIDE, ps.DYNAMIC_SIDE, "two_class")
    assert seg.probability(_red_scene()).shape == (ps.DYNAMIC_SIDE, ps.DYNAMIC_SIDE)


def test_model_without_a_person_map_is_refused(tmp_path):
    with pytest.raises(ValueError, match="no person map"):
        ps.PersonSegmenter(path=conv1x1(tmp_path / "x.onnx", np.ones((7, 3), np.float32)), compute_units="cpu_only")


@pytest.mark.parametrize("cat", ["semantic_segmentation", "background_segmentation"])
def test_check_offers_these_layouts_for_the_background(tmp_path, cat):
    for make in (_matte_model, _voc_model):
        rep = mc.check(make(tmp_path / "m.onnx"), cat, providers=["CPUExecutionProvider"], runs=1)
        assert rep.fits and rep.used_by.startswith("background (person segmentation")
        assert mc.usable_for({"report": rep.as_dict()}) == "background"
    other = mc.check(conv1x1(tmp_path / "o.onnx", np.ones((7, 3), np.float32)), cat,
                     providers=["CPUExecutionProvider"], runs=1)
    assert other.fits and other.used_by == ""                    # 7 classes: which one is a person is unknown


def test_person_masks_follow_the_model_output_size(tmp_path):
    seg = ps.PersonSegmenter(path=_matte_model(tmp_path / "m.onnx", side=64), compute_units="cpu_only")
    det = ps.PersonMaskDetector(NS(name="fake"), segmenter=seg)
    m = det.mask_for(_red_scene(), (50, 30, 110, 90))
    assert m is not None and m[m.shape[0] // 4: -m.shape[0] // 4, m.shape[1] // 4: -m.shape[1] // 4].min() > 220


def test_app_uses_a_registered_matte_model_for_the_background(paths, tmp_path):
    from vcstudio.app.application import AppContext
    ctx = AppContext(paths, configure_logging=False)
    try:
        rep = mc.check(_matte_model(tmp_path / "matte.onnx"), "semantic_segmentation",
                       providers=["CPUExecutionProvider"], runs=1)
        mc.register(rep, ctx.paths.models_dir, "matte")
        ctx.settings.set("models", {"background": "matte"})
        seg = ctx.background.person_factory()
        assert isinstance(seg, ps.PersonSegmenter) and seg.layout.kind == "matte"
    finally:
        ctx.shutdown()
