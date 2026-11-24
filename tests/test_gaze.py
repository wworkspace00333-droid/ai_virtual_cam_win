"""0.7.0 gaze tracking and eye contact correction: synthetic eyes of known geometry (mesh points placed by hand, a
drawn iris), the real face mesh on a real photo, the pipeline stage and the calibration."""
from __future__ import annotations

import math
import threading
import time
from types import SimpleNamespace as NS

import cv2
import numpy as np
import pytest

from vcstudio.ai import gaze
from vcstudio.ai.vision import FaceInfo, VisionConfig, VisionResult, VisionService
from vcstudio.core.frame import Frame
from vcstudio.replacement import eye_contact as ec

W, H = 320, 200
A, B, R = 20.0, 6.0, 5.0              # eye half width / half height, iris radius (pixels)
CENTRES = {"left": (120.0, 100.0), "right": (200.0, 100.0)}


def _eye_ring(eye: str, cx: float, cy: float, b: float = B) -> np.ndarray:
    """The 16 contour points in MediaPipe's order: lower lid corner to corner, then the upper lid back."""
    if eye == "left":                 # 33 (left corner) .. bottom .. 133 (right corner), then the top back
        th = np.r_[np.linspace(math.pi, 0, 9), np.linspace(0, -math.pi, 9)[1:-1]]
    else:                             # 263 (right corner) .. bottom .. 362 (left corner), then the top back
        th = np.r_[np.linspace(0, math.pi, 9), np.linspace(math.pi, 2 * math.pi, 9)[1:-1]]
    return np.stack([cx + A * np.cos(th), cy + b * np.sin(th)], 1)


def mesh(iris=(0.0, 0.0), b: float = B, shift=(0.0, 0.0)) -> np.ndarray:
    """(478, 3) points: both eyes with irises offset by `iris` pixels, the whole face moved by `shift`."""
    p = np.zeros((478, 3), np.float32)
    for eye, (cx, cy) in CENTRES.items():
        cx, cy = cx + shift[0], cy + shift[1]
        e = gaze.EYES[eye]
        p[list(e["contour"]), :2] = _eye_ring(eye, cx, cy, b)
        ix, iy = cx + iris[0], cy + iris[1]
        p[e["iris"], :2] = (ix, iy)
        for k, (dx, dy) in zip(e["ring"], ((R, 0), (0, -R), (-R, 0), (0, R))):
            p[k, :2] = (ix + dx, iy + dy)
    return p


def picture(iris=(0.0, 0.0)) -> np.ndarray:
    img = np.full((H, W, 4), (120, 140, 170, 255), np.uint8)
    for cx, cy in CENTRES.values():
        cv2.ellipse(img, (int(cx), int(cy)), (int(A), int(B)), 0, 0, 360, (235, 235, 235, 255), -1)
        cv2.circle(img, (int(cx + iris[0]), int(cy + iris[1])), int(R), (40, 30, 20, 255), -1)
    return img


def dark_centroid(img, cx, cy):
    roi = img[int(cy - B):int(cy + B) + 1, int(cx - A):int(cx + A) + 1, 0].astype(np.float32)
    w = np.clip(110 - roi, 0, None)                                  # the iris (40) only, not skin (120) or white
    yy, xx = np.mgrid[0:roi.shape[0], 0:roi.shape[1]]
    return float((xx * w).sum() / w.sum()) + int(cx - A), float((yy * w).sum() / w.sum()) + int(cy - B)


def test_iris_offset_and_openness():
    m = gaze.measure_eye(mesh(), "left")
    assert abs(m.h) < 1e-3 and abs(m.v) < 1e-3 and abs(m.openness - 2 * B / (2 * A)) < 1e-3
    assert abs(m.radius - R) < 1e-3 and abs(m.half - A) < 1e-3
    r = gaze.measure_eye(mesh(iris=(5, -2)), "right")
    assert abs(r.h - 0.25) < 1e-3 and abs(r.v + 0.1) < 1e-3       # right and up in the picture, for either eye
    assert gaze.measure_eye(np.zeros((478, 3)), "left") is None


def test_eye_angles_and_camera_direction():
    yaw, pitch = gaze.eye_angles(0.25, 0.0)
    assert abs(yaw - math.degrees(math.asin(0.25 * gaze.EYE_GAIN))) < 1e-6 and pitch == 0
    assert gaze.eye_angles(0.0, 0.2)[1] < 0                          # iris lower: looking down
    assert gaze.camera_direction(W / 2, H / 2, W, H) == (0.0, 0.0)
    yaw, pitch = gaze.camera_direction(W, H, W, H)                   # face at the lower right
    assert yaw < 0 and pitch > 0                                     # must look left and up to see the camera


def test_gaze_directions_with_head_pose_compensation():
    g = gaze.estimate(mesh(), None, W, H)
    assert g.at_camera and g.direction == "at_camera" and g.confidence > 0.5
    assert gaze.estimate(mesh(iris=(0, 4)), None, W, H).direction == "down"
    assert gaze.estimate(mesh(iris=(-8, 0)), None, W, H).direction == "left"
    turned = gaze.estimate(mesh(), NS(yaw=20.0, pitch=0.0), W, H)    # eyes centred in a turned head
    assert turned.direction == "right" and abs(turned.off_yaw - 20.0) < 0.5
    back = gaze.estimate(mesh(iris=(-6, 0)), NS(yaw=20.0, pitch=0.0), W, H)
    assert back.at_camera                                            # head right, eyes left: looks at the camera
    edge = gaze.estimate(mesh(shift=(110, 0)), None, W, H)           # face near the right edge, eyes straight
    assert edge.off_yaw > 10 and not edge.at_camera


def test_blink_and_calibration():
    closed = gaze.estimate(mesh(b=1.5), None, W, H)
    assert closed.confidence == 0 and closed.direction == "unknown" and not closed.at_camera
    down = gaze.estimate(mesh(iris=(0, 3)), None, W, H)
    cal = {"yaw": down.off_yaw, "pitch": down.off_pitch}             # "I am looking at the camera" while looking down
    assert gaze.estimate(mesh(iris=(0, 3)), None, W, H, calibration=cal).at_camera


def test_tracker_smooths_measures_stability_and_skips_blinks():
    tr = gaze.GazeTracker()
    g = gaze.estimate(mesh(), None, W, H)
    jump = gaze.estimate(mesh(iris=(8, 0)), None, W, H)
    out = None
    for i in range(10):
        out = tr.update(i * 0.05, [((160, 100), g)])
    assert out[0].stability == 1.0 and out[0].off_yaw == g.off_yaw
    for i in range(10, 20):
        out = tr.update(i * 0.05, [((160, 100), jump if i % 2 else g)])
    assert out[0].stability < 0.5 and g.off_yaw < out[0].off_yaw < jump.off_yaw
    closed = gaze.estimate(mesh(b=1.5), None, W, H)
    n = len(tr.tracks[0]["hist"])
    tr.update(1.0, [((160, 100), closed)])
    assert len(tr.tracks[0]["hist"]) <= n                            # a blink adds nothing
    two = tr.update(1.05, [((160, 100), g), ((600, 100), g)])
    assert len(two) == 2 and len(tr.tracks) == 2                      # a second face gets its own track


def test_target_shift_signs_and_cap():
    g = gaze.Gaze(0, 0, off_yaw=0, off_pitch=-14.0, confidence=1)    # looks down
    dx, dy = ec.target_shift(g, 20.0, 1.0)
    assert abs(dx) < 1e-6 and dy < 0                                 # iris goes up
    dx, _ = ec.target_shift(gaze.Gaze(0, 0, off_yaw=10, off_pitch=0, confidence=1), 20.0, 1.0)
    assert dx < 0
    dx, dy = ec.target_shift(gaze.Gaze(0, 0, off_yaw=0, off_pitch=-60, confidence=1), 20.0, 1.0)
    assert abs(math.hypot(dx, dy) - ec.MAX_SHIFT * 20.0) < 1e-6
    assert ec.target_shift(g, 20.0, 0.5)[1] == pytest.approx(ec.target_shift(g, 20.0, 1.0)[1] / 2)


def test_eye_contact_moves_the_iris_inside_the_eye_only():
    img = picture(iris=(0, 3))                                       # looking down
    pts = mesh(iris=(0, 3))
    g = gaze.estimate(pts, None, W, H)
    assert g.direction == "down" and g.confidence >= ec.MIN_CONFIDENCE
    before = img.copy()
    n = ec.correct(img, pts, g, 1.0)
    assert n == 2
    for cx, cy in CENTRES.values():
        y0 = dark_centroid(before, cx, cy)[1]
        y1 = dark_centroid(img, cx, cy)[1]
        assert y1 < y0 - 1.5                                         # moved up, towards the eye's centre
    inside = np.zeros((H, W), np.uint8)
    for cx, cy in CENTRES.values():
        cv2.ellipse(inside, (int(cx), int(cy)), (int(A) + 1, int(B) + 1), 0, 0, 360, 255, -1)
    assert np.array_equal(img[inside == 0], before[inside == 0])     # lids, skin, everything else untouched


def test_eye_contact_falls_back_without_changing_anything():
    img = picture(iris=(0, 3))
    before = img.copy()
    closed = gaze.estimate(mesh(b=1.5), None, W, H)
    assert ec.correct(img, mesh(b=1.5), closed, 1.0) == 0
    away = gaze.estimate(mesh(iris=(-14, 0)), NS(yaw=-20.0, pitch=0.0), W, H)   # clearly looking elsewhere
    assert math.hypot(away.off_yaw, away.off_pitch) > ec.MAX_CORRECT_DEG
    assert ec.correct(img, mesh(iris=(-14, 0)), away, 1.0) == 0
    assert ec.correct(img, mesh(iris=(0, 3)), gaze.estimate(mesh(iris=(0, 3)), None, W, H), 0.0) == 0
    assert ec.correct(img, mesh(), None, 1.0) == 0
    assert np.array_equal(img, before)


def test_eye_contact_cost_at_720p():
    big = np.full((720, 1280, 4), 128, np.uint8)
    k = 4.0                                                          # eyes 4x larger than the synthetic ones
    pts = mesh(iris=(0, 3))
    pts[:, :2] = pts[:, :2] * k + (300, 200)
    g = gaze.estimate(pts, None, 1280, 720)
    g = gaze.Gaze(g.yaw, g.pitch, 0.0, -10.0, 1.0, 1.0, g.eyes)
    ec.correct(big, pts, g, 1.0)
    t = time.perf_counter()
    for _ in range(20):
        assert ec.correct(big, pts, g, 1.0) == 2
    ms = (time.perf_counter() - t) * 1000 / 20
    print(f"eye contact, one face with 160 px wide eyes at 720p: {ms:.2f} ms")
    assert ms < 15


def test_real_photo_irises_sit_between_their_eye_corners():
    from vcstudio.ai import face_mesh, head_pose
    from vcstudio.ai.onnx_engine import YuNetFaceDetector, yunet_path
    if not (face_mesh.available() and yunet_path().exists()):
        pytest.skip("face models not present")
    bgr = cv2.imread("tests/data/bus.jpg")
    a = cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA)
    faces, _ = YuNetFaceDetector().detect(Frame(0, 0, 0, a.shape[1], a.shape[0], array=a, source="t"))
    fm = face_mesh.FaceMesh()
    got = 0
    for f in faces:
        m = fm.run(bgr, (f.x0, f.y0, f.x1, f.y1), f.points)
        if m is None:
            continue
        for eye in ("left", "right"):
            e = gaze.measure_eye(m.points, eye)
            assert e is not None and abs(e.h) < 0.6 and abs(e.v) < 0.6
        g = gaze.estimate(m.points, head_pose.estimate(f.points, a.shape[1], a.shape[0]), a.shape[1], a.shape[0],
                          m.score)
        assert g is not None and 0 <= g.confidence <= 1
        got += 1
    assert got >= 1


def test_config_turns_the_mesh_on_and_raises_the_rate():
    class S:
        def __init__(self, d):
            self.d = d

        def get(self, k):
            return self.d[k]

        def set(self, k, v):
            self.d[k] = v

    c = VisionConfig.from_settings(S({"vision": {"enabled": True, "fps": 6, "eye_contact": True,
                                                 "eye_contact_strength": 3, "gaze_calibration": {"yaw": 2,
                                                                                                 "pitch": -4}}}))
    assert c.needs_mesh and c.needs_gaze and c.rate == 15.0 and c.eye_strength == 1.0
    assert c.calibration == (2.0, -4.0)
    assert VisionConfig.from_settings(S({"vision": {"gaze": True}})).rate == 6.0


def _service(settings_d):
    class S:
        def get(self, k):
            return settings_d[k]

        def set(self, k, v):
            settings_d[k] = v
    vs = VisionService(None, S())
    vs.cfg = VisionConfig.from_settings(vs.settings)
    return vs


def _result(pts, iris=(0.0, 3.0)):
    p = mesh(iris=iris)
    r = VisionResult(pts, W, H)
    fi = FaceInfo((90, 80, 230, 140), np.zeros((5, 2), np.float32), None, NS(points=p, score=1.0))
    r.faces.append(fi)
    return r


def test_service_computes_gaze_and_calibrates():
    d = {"vision": {"enabled": True, "gaze": True}}
    vs = _service(d)
    r = _result(1.0)
    vs._gaze(r, vs.cfg)
    assert r.faces[0].gaze is not None and r.faces[0].gaze.direction == "down"
    vs.slot.put(r)
    cal = vs.calibrate()
    assert cal == d["vision"]["gaze_calibration"] and cal["pitch"] < 0
    vs.cfg = VisionConfig.from_settings(vs.settings)
    r2 = _result(1.1)
    vs.gaze_tracker.reset()
    vs._gaze(r2, vs.cfg)
    assert r2.faces[0].gaze.at_camera                               # calibrated: this is "at the camera" now
    vs.slot.clear()
    assert vs.calibrate() is None


def test_pipeline_corrects_only_with_a_fresh_mesh():
    from vcstudio.core.video_pipeline import VideoPipeline
    d = {"vision": {"enabled": True, "eye_contact": True}}
    vs = _service(d)
    r = _result(5.0)
    vs._gaze(r, vs.cfg)
    vs.slot.put(r)
    me = NS(_lock=threading.Lock(), _stats=NS(last_error=""))
    img = picture(iris=(0, 3))
    out = Frame(1, 5.05, 5.05, W, H, array=img.copy(), source="cam")
    res = VideoPipeline._eye_contact(me, vs, out, out)
    assert res is not out and res.source == "cam+eyes" and vs.eyes_corrected == 2
    with res.bgra() as a:
        assert not np.array_equal(a, img)
    late = Frame(2, 5.0 + ec.MAX_AGE_S + 0.05, 0, W, H, array=img.copy(), source="cam")
    assert VideoPipeline._eye_contact(me, vs, late, late) is late and vs.eye_skipped == 1
    with late.bgra() as a:
        assert np.array_equal(a, img)
