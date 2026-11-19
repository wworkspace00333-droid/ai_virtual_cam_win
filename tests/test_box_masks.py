"""Silhouettes without a segmentation model (roadmap P1.2): YOLOX boxes + a SAM 2.1 mask per box."""
from __future__ import annotations

import sys

import numpy as np
import pytest

from vcstudio.ai.detection import Detection, DetectionConfig
from vcstudio.core.profiles import default_profile_body
from vcstudio.roi.model import ROI


class FakeDet:
    name = "fake"
    loaded = None

    def __init__(self, dets):
        self.dets = dets

    def detect(self, frame, conf=0.25, iou=0.5):
        return [Detection(d.label, d.confidence, d.x0, d.y0, d.x1, d.y1) for d in self.dets], 1.0


class FakeSam:
    def __init__(self):
        self.images = 0
        self.boxes = []

    def set_image(self, img):
        self.images += 1
        self._shape = img.shape[:2]

    def mask_from_box(self, box):
        self.boxes.append(box)
        m = np.zeros(self._shape, bool)
        x0, y0, x1, y1 = (int(v) for v in box)
        m[y0 + 2:y1 - 2, x0 + 2:x1 - 2] = True
        return m, 0.9


def frame(w=320, h=240):
    from vcstudio.core.frame import Frame
    return Frame(1, 0, 0, w, h, array=np.zeros((h, w, 4), np.uint8))


def test_masks_only_for_the_wanted_classes_and_at_most_n():
    from vcstudio.ai.open_vocab import BoxMaskDetector
    dets = [Detection("person", 0.9 - i * 0.1, 10 + i * 30, 10, 40 + i * 30, 100) for i in range(6)]
    dets.append(Detection("car", 0.95, 200, 120, 300, 200))
    sam = FakeSam()
    bm = BoxMaskDetector(FakeDet(dets), mask_classes=["person"], max_masks=4, segmenter=sam)
    out, _ = bm.detect(frame())
    assert sam.images == 1 and len(sam.boxes) == 4 and bm.masked == 4       # one encode, four masks
    masked = [d for d in out if d.mask is not None]
    assert {d.label for d in masked} == {"person"}
    assert sorted(d.confidence for d in masked) == pytest.approx([0.6, 0.7, 0.8, 0.9])   # the most confident
    assert next(d for d in out if d.label == "car").mask is None
    m = masked[0].mask
    assert m.dtype == np.uint8 and m.max() == 255 and m[0, 0] == 0        # box-local silhouette format
    sam2 = FakeSam()
    out, _ = BoxMaskDetector(FakeDet([]), segmenter=sam2).detect(frame())
    assert out == [] and sam2.images == 0                                 # nothing to mask: no SAM work


def test_mask_classes_come_from_the_silhouette_regions():
    def obj_roi(classes):
        r = ROI(mode="auto", classes=classes)
        r.replacement = {"enabled": True, "mode": "object"}
        return r.to_dict()
    b = default_profile_body()
    b["rois"] = [obj_roi(["person"]), obj_roi(["dog"])]
    c = DetectionConfig.from_profile(b)
    assert c.need_masks and c.mask_classes == ["dog", "person"]
    b["rois"].append(obj_roi([]))                                          # one region takes any class
    assert DetectionConfig.from_profile(b).mask_classes == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows models")
def test_engine_uses_box_masks_when_no_segmentation_model(tmp_path, monkeypatch):
    from vcstudio.ai import open_vocab
    from vcstudio.ai.engine import AIEngine
    from vcstudio.ai.model_manager import ModelManager
    from vcstudio.core.capture_supervisor import CaptureSupervisor
    eng = AIEngine(CaptureSupervisor(), ModelManager(tmp_path))
    real = eng.models.available
    monkeypatch.setattr(eng.models, "available", lambda *a, **k: {k2: v for k2, v in real().items() if v.task != "segment"})
    monkeypatch.setattr(open_vocab, "sam_available", lambda: True)
    monkeypatch.setattr(open_vocab, "PromptSegmenter", lambda *a, **k: FakeSam())
    from vcstudio.ai import person_segmentation
    cfg = DetectionConfig(need_masks=True, mask_classes=["person"], detector_model="yolox_nano")
    eng.configure(cfg, "balanced")
    eng._ensure_models(cfg, "balanced")
    assert isinstance(eng._detector, person_segmentation.PersonMaskDetector)   # people only: the shipped PP-HumanSeg
    assert eng._detector.det.name == "yolox_nano"
    eng._ensure_models(DetectionConfig(need_masks=True, mask_classes=["dog"], detector_model="yolox_nano"), "balanced")
    det = eng._detector
    assert isinstance(det, open_vocab.BoxMaskDetector) and det.det.name == "yolox_nano"   # other classes: SAM 2.1
    eng._ensure_models(DetectionConfig(need_masks=True, mask_classes=["cat"], detector_model="yolox_nano"), "balanced")
    assert eng._detector is det and det.mask_classes == ["cat"]            # no reload for new classes
    monkeypatch.setattr(open_vocab, "sam_available", lambda: False)
    eng._ensure_models(DetectionConfig(need_masks=True, detector_model="yolox_nano"), "balanced")
    assert isinstance(eng._detector, person_segmentation.PersonMaskDetector)   # no SAM: people still get silhouettes
    eng._ensure_models(DetectionConfig(detector_model="yolox_nano"), "balanced")
    assert not isinstance(eng._detector, open_vocab.BoxMaskDetector)       # masks no longer needed: plain detector


@pytest.mark.skipif(sys.platform != "win32", reason="Windows models")
def test_real_sam_silhouettes_on_the_sample_photo():
    from vcstudio.ai import open_vocab
    if not open_vocab.sam_available():
        pytest.skip("SAM 2.1 not downloaded (Detection page -> Download models)")
    from vcstudio.ai.backend import make_detector
    from vcstudio.ai.model_manager import ModelManager
    from vcstudio.ai.testimages import load_bgra
    from vcstudio.core.frame import Frame
    from vcstudio.core.resources import models_dir, sample_photo
    mm = ModelManager(models_dir())
    det = make_detector(mm.load(mm.available()["yolox_nano"], "cpu"))
    img = load_bgra(sample_photo())
    f = Frame(1, 0, 0, img.shape[1], img.shape[0], array=img)
    bm = open_vocab.BoxMaskDetector(det, "cpu", ["person"])
    out, ms = bm.detect(f, 0.3, 0.5)
    people = [d for d in out if d.label == "person" and d.mask is not None]
    assert len(people) >= 3
    for d in people:
        fill = (d.mask > 127).mean()
        assert 0.15 < fill < 0.95, fill                                    # a silhouette, not empty or the whole box
    print(f"\nSAM silhouettes: {len(people)} people, {ms:.0f} ms per frame incl. detector (CPU)")


class FakeSeg:
    def __init__(self):
        self.crops = []

    def probability(self, img):
        self.crops.append(img.shape[:2])
        return np.ones((192, 192), np.float32)


def test_person_masks_only_on_people_from_a_crop_each():
    from vcstudio.ai.person_segmentation import PersonMaskDetector
    dets = [Detection("person", 0.9 - i * 0.1, 10 + i * 30, 10, 40 + i * 30, 100) for i in range(6)]
    dets += [Detection("car", 0.95, 200, 120, 300, 200), Detection("person", 0.99, 5, 5, 7, 7)]   # last: too small
    seg = FakeSeg()
    pm = PersonMaskDetector(FakeDet(dets), max_masks=4, segmenter=seg)
    out, _ = pm.detect(frame())
    assert len(seg.crops) == 3 and pm.masked == 3          # the tiny box takes a slot but gets no mask
    assert next(d for d in out if d.label == "car").mask is None
    m = next(d for d in out if d.mask is not None).mask
    assert m.dtype == np.uint8 and m.shape == (90, 30) and m.min() == 255   # box-local, box-sized, from the crop


@pytest.mark.skipif(sys.platform != "win32", reason="Windows models")
def test_real_person_silhouettes_on_the_sample_photo():
    from vcstudio.ai import person_segmentation
    if not person_segmentation.available():
        pytest.skip("PP-HumanSeg missing (scripts/fetch_models.py)")
    from vcstudio.ai.backend import make_detector
    from vcstudio.ai.model_manager import ModelManager
    from vcstudio.ai.testimages import load_bgra
    from vcstudio.core.frame import Frame
    from vcstudio.core.resources import models_dir, sample_photo
    mm = ModelManager(models_dir())
    det = make_detector(mm.load(mm.available()["yolox_nano"], "cpu"))
    img = load_bgra(sample_photo())
    f = Frame(1, 0, 0, img.shape[1], img.shape[0], array=img)
    pm = person_segmentation.PersonMaskDetector(det, "cpu")
    pm.detect(f, 0.3, 0.5)
    out, ms = pm.detect(f, 0.3, 0.5)
    people = [d for d in out if d.label == "person" and d.mask is not None]
    assert len(people) >= 3
    for d in people:
        fill = (d.mask > 127).mean()
        assert 0.15 < fill < 0.95, fill                                    # a silhouette, not empty or the whole box
    print(f"\nPP-HumanSeg silhouettes: {len(people)} people, {ms:.0f} ms per frame incl. detector (CPU)")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows models")
def test_distant_faces_are_found_in_enlarged_head_crops():
    """D25: a face of ~14 px in a 1080p frame is below what the 640 px pass sees; the person's head crop finds it,
    and a person whose face the frame pass already found gets no second look."""
    import cv2
    from vcstudio.ai.onnx_engine import YuNetFaceDetector, yunet_path
    from vcstudio.core.frame import Frame
    if not yunet_path().exists():
        pytest.skip("YuNet missing")
    src = cv2.cvtColor(cv2.imread("tests/data/bus.jpg"), cv2.COLOR_BGR2BGRA)          # two faces ~40 px
    fd = YuNetFaceDetector()

    def scene(scale):
        canvas = np.full((1080, 1920, 4), 90, np.uint8)
        sm = cv2.resize(src, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        canvas[100:100 + sm.shape[0], 700:700 + sm.shape[1]] = sm
        # the two people with faces in bus.jpg (boxes from yolox_nano on the original), placed like the photo
        people = [Detection("person", 0.9, 700 + x0 * scale, 100 + y0 * scale, 700 + x1 * scale, 100 + y1 * scale)
                  for x0, y0, x1, y1 in ((48, 398, 245, 902), (221, 405, 344, 857))]
        return Frame(1, 0, 0, 1920, 1080, array=canvas), people

    f, people = scene(0.35)
    assert fd.detect(f)[0] == []                                           # too small for the frame pass
    faces, _ = fd.detect(f, people)
    assert len(faces) == 2 and fd.head_crops == 2
    f, people = scene(0.6)
    faces, _ = fd.detect(f, people)
    assert len(faces) == 2 and fd.head_crops == 0                          # found already: no crops, no duplicates
