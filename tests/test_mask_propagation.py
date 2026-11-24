"""0.7.0 temporal mask propagation: SAM on key frames, optical flow in between (synthetic textured scenes with a
known object mask; a stand-in SAM returns the true mask so the carried one can be compared with it)."""
from __future__ import annotations

import time

import cv2
import numpy as np

from vcstudio.ai.detection import Detection
from vcstudio.ai.open_vocab import BoxMaskDetector
from vcstudio.core.frame import Frame

W, H = 320, 240
RNG = np.random.default_rng(3)
BG = cv2.GaussianBlur(RNG.integers(0, 255, (H, W), np.uint8), (0, 0), 1.5)
OBJ_TEX = cv2.GaussianBlur(RNG.integers(0, 255, (H, W), np.uint8), (0, 0), 1.2)


def blob(dx=0, arm=0):
    """An object: a body ellipse plus an arm that swings by `arm` pixels (the box stays about the same)."""
    m = np.zeros((H, W), np.uint8)
    cv2.ellipse(m, (120 + dx, 120), (35, 60), 0, 0, 360, 255, -1)
    cv2.line(m, (120 + dx, 100), (175 + dx, 80 + arm), 255, 12)
    return m > 0


def scene(mask, dx=0):
    g = BG.copy()
    tex = np.roll(OBJ_TEX, dx, axis=1)                    # the object's texture moves with it
    g[mask] = tex[mask]
    img = cv2.cvtColor(g, cv2.COLOR_GRAY2BGRA)
    return img


def box_of(mask):
    ys, xs = np.nonzero(mask)
    return float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1)


class Det:
    name, loaded = "fake", None

    def __init__(self):
        self.boxes = []

    def detect(self, frame, conf=0.25, iou=0.5):
        return [Detection("person", 0.9, *b) for b in self.boxes], 1.0


class TrueSam:
    def __init__(self):
        self.calls = 0
        self.truth = None

    def set_image(self, img):
        pass

    def mask_from_box(self, box):
        self.calls += 1
        return self.truth.copy(), 0.9


def iou_local(det, truth):
    from vcstudio.ai.open_vocab import local_mask
    ref = local_mask(truth, (det.x0, det.y0, det.x1, det.y1)) > 127
    m = cv2.resize(det.mask, (ref.shape[1], ref.shape[0])) > 127
    return (m & ref).sum() / max(1, (m | ref).sum())


def run(bm, det, sam, mask, dx, t):
    sam.truth = mask
    det.boxes = [box_of(mask)]
    out, _ = bm.detect(Frame(1, t, t, W, H, array=scene(mask, dx)))
    return out[0]


def test_flow_carries_the_mask_between_key_frames_and_follows_deformation():
    det, sam = Det(), TrueSam()
    bm = BoxMaskDetector(det, segmenter=sam, keyframe_s=1.0)
    m0 = blob()
    run(bm, det, sam, m0, 0, 0.0)
    assert sam.calls == 1                                                # key frame
    m1 = blob(dx=4, arm=10)
    d1 = run(bm, det, sam, m1, 4, 0.1)
    assert sam.calls == 1 and bm.carried == 1 and d1.source.endswith("+flow")
    assert iou_local(d1, m1) > 0.85
    m2 = blob(dx=8, arm=22)
    d2 = run(bm, det, sam, m2, 8, 0.2)
    assert sam.calls == 1 and iou_local(d2, m2) > 0.8
    d3 = run(bm, det, sam, blob(dx=8, arm=22), 8, 1.1)                  # keyframe_s passed
    assert sam.calls == 2 and d3.source.endswith("+sam")


def test_new_objects_and_occlusion_force_a_key_frame():
    det, sam = Det(), TrueSam()
    bm = BoxMaskDetector(det, segmenter=sam, keyframe_s=5.0)
    m0 = blob()
    run(bm, det, sam, m0, 0, 0.0)
    occluded = blob(dx=3)
    sam.truth = occluded
    det.boxes = [box_of(occluded)]
    other = cv2.cvtColor(np.ascontiguousarray(np.flipud(BG)), cv2.COLOR_GRAY2BGRA)   # the view is covered
    bm.detect(Frame(2, 0.1, 0.1, W, H, array=other))
    assert sam.calls == 2 and bm.propagator.failed >= 1                  # flow not trusted: SAM again
    det.boxes = [box_of(m0), (250.0, 20.0, 300.0, 90.0)]                 # a second object appears
    sam.truth = m0
    bm.detect(Frame(3, 0.2, 0.2, W, H, array=scene(m0)))
    assert sam.calls == 4                                                # a key frame for both


def test_keyframe_zero_means_sam_every_frame_and_memory_is_bounded():
    det, sam = Det(), TrueSam()
    bm = BoxMaskDetector(det, segmenter=sam, keyframe_s=0)
    for i in range(3):
        run(bm, det, sam, blob(dx=i), i, i * 0.1)
    assert sam.calls == 3 and bm.propagator is None
    bm2 = BoxMaskDetector(Det(), segmenter=TrueSam(), keyframe_s=1.0)
    run(bm2, bm2.det, bm2.sam, blob(), 0, 0.0)
    run(bm2, bm2.det, bm2.sam, blob(), 0, 10.0)
    assert len(bm2.propagator.memory) == 1                               # old entries forgotten


def test_carry_cost():
    det, sam = Det(), TrueSam()
    bm = BoxMaskDetector(det, segmenter=sam, keyframe_s=100.0)
    run(bm, det, sam, blob(), 0, 0.0)
    t = time.perf_counter()
    for i in range(1, 11):
        run(bm, det, sam, blob(dx=i % 3), i % 3, i * 0.01)
    ms = (time.perf_counter() - t) * 100
    print(f"flow carry, one object: {ms:.1f} ms")
    assert sam.calls == 1 and ms < 40


def test_keyframe_setting_reaches_the_detector():
    from vcstudio.ai.detection import DetectionConfig
    assert DetectionConfig.from_profile({"detection": {"mask_keyframe_s": 2.5}}).mask_keyframe_s == 2.5
    assert DetectionConfig.from_profile({"detection": {"mask_keyframe_s": 99}}).mask_keyframe_s == 10.0
    assert DetectionConfig.from_profile({}).mask_keyframe_s == 1.0
    bm = BoxMaskDetector(Det(), segmenter=TrueSam(), keyframe_s=1.0)
    bm.set_keyframe(3.0)
    assert bm.propagator.keyframe_s == 3.0
    bm.set_keyframe(0)
    assert bm.propagator is None
    bm.set_keyframe(0.5)
    assert bm.propagator is not None and bm.propagator.keyframe_s == 0.5
