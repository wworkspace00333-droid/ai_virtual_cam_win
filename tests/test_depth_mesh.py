"""0.6.0: dense face mesh and depth (downloaded models, checked on a real photo before use)."""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from vcstudio.ai import depth, face_mesh
from vcstudio.core.frame import Frame
from vcstudio.core.resources import sample

needs_mesh = pytest.mark.skipif(not face_mesh.available(), reason="face mesh model not downloaded")
needs_depth = pytest.mark.skipif(not depth.available(), reason="depth model not downloaded")


def bus():
    img = cv2.imread(str(sample("bus.jpg")))
    return img, cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)


@needs_mesh
def test_mesh_agrees_with_yunet_on_real_faces_and_rejects_a_wall():
    from vcstudio.ai.onnx_engine import YuNetFaceDetector
    img, a = bus()
    faces, _ = YuNetFaceDetector().detect(Frame(0, 0, 0, a.shape[1], a.shape[0], array=a, source="t"))
    fm = face_mesh.FaceMesh()
    assert len(faces) == 2
    for f in faces:
        m = fm.run(img, (f.x0, f.y0, f.x1, f.y1), f.points)
        assert m is not None and m.points.shape == (478, 3)          # incl. sunglasses (low presence, still a face)
        size = f.x1 - f.x0
        nose = m.points[face_mesh.NOSE_TIP, :2]
        assert np.linalg.norm(nose - f.points[2]) < 0.2 * size        # nose tip where YuNet puts it
        mouth = m.points[[face_mesh.MOUTH_LEFT, face_mesh.MOUTH_RIGHT], :2]
        assert np.linalg.norm(mouth - f.points[3:5], axis=1).max() < 0.25 * size
        oval = m.oval()
        inside = cv2.pointPolygonTest(oval.reshape(-1, 1, 2), (float(nose[0]), float(nose[1])), False)
        assert inside > 0                                             # the outline surrounds the face
    assert fm.run(img, (600, 100, 700, 200)) is None                  # a wall is not a face


def test_mesh_crop_transform_levels_the_eyes():
    M = face_mesh.FaceMesh.crop_transform((100, 100, 200, 200), eyes=((120, 140), (180, 160)))
    pts = cv2.transform(np.array([[[120, 140], [180, 160]]], np.float32), M)[0]
    assert abs(pts[0, 1] - pts[1, 1]) < 1e-3                          # eyes on one row in the crop
    assert 0 < pts[0, 0] < face_mesh.SIZE and 0 < pts[1, 0] < face_mesh.SIZE


@needs_depth
def test_depth_orders_people_before_the_building():
    img, a = bus()
    d = depth.DepthSegmenter()
    near = d.probability(a)
    h, w = near.shape
    person = near[int(h * 0.55):int(h * 0.75), int(w * 0.08):int(w * 0.25)].mean()   # the man on the left
    building = near[: int(h * 0.15), :].mean()                                        # the facade at the top
    assert person > building + 0.3 and 0.0 <= near.min() and near.max() <= 1.0


def test_depth_input_size_keeps_aspect_in_multiples_of_14():
    assert depth.input_size(1920, 1080, 266) == (266, 154)
    w, h = depth.input_size(640, 480, 300)
    assert w % 14 == 0 and h % 14 == 0 and abs(w / h - 4 / 3) < 0.1


def test_background_switches_to_the_depth_source():
    from vcstudio.replacement import background as bgm

    class Cap:
        def __init__(self):
            from vcstudio.core.frame_queue import LatestSlot
            self.slot = LatestSlot()

    class FakeDepth:
        backend = "fake"

        def probability(self, img):
            return np.linspace(0, 1, 64, dtype=np.float32)[None, :].repeat(36, 0)   # nearer to the right
    svc = bgm.BackgroundService(Cap(), assets=None, segmenter_factory=lambda: None)
    svc.depth_factory = FakeDepth
    svc.load_profile({"background": {"mode": "color", "color": "#00ff00", "source": "depth", "fps": 15}})
    assert svc.seg._factory is FakeDepth and svc.seg.fps == 4.0
    svc.seg.masks.put(FakeDepth().probability(None))
    f = np.full((36, 64, 4), 90, np.uint8)
    out = svc.apply(Frame(1, 1.0, 1.0, 64, 36, array=f, source="t"))
    with out.bgra() as o:
        assert tuple(o[18, 2, :3]) == (0, 255, 0) and tuple(o[18, 61, :3]) == (90, 90, 90)   # far replaced, near kept
    svc.load_profile({"background": {"mode": "color", "source": "person"}})
    assert svc.seg._factory is svc.person_factory


@needs_mesh
def test_vision_service_adds_the_mesh():
    from vcstudio.ai.onnx_engine import YuNetFaceDetector
    from vcstudio.ai.vision import VisionConfig, VisionService
    img, a = bus()
    vs = VisionService(None, None)
    r = vs.analyse(Frame(1, 1.0, 1.0, a.shape[1], a.shape[0], array=a, source="t"),
                   {"face": YuNetFaceDetector(), "mesh": face_mesh.FaceMesh()},
                   VisionConfig(enabled=True, face=True, pose=False, mesh=True))
    assert r.faces and all(f.mesh is not None for f in r.faces) and "mesh" in r.ms
    from vcstudio.ai.vision import draw
    out = a.copy()
    draw(out, r)
    assert not np.array_equal(out, a)
