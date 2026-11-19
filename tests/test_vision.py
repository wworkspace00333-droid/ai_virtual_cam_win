"""0.6.0 Phase 3: face landmarks + head pose, body pose, the Vision service and its overlay in the pipeline."""
from __future__ import annotations

import time

import cv2
import numpy as np
import pytest

from vcstudio.ai import head_pose as hp
from vcstudio.ai import pose
from vcstudio.core.frame import Frame
from vcstudio.core.resources import sample

needs_pose = pytest.mark.skipif(not pose.available(), reason="pose models not downloaded")


def bus_frame():
    img = cv2.imread(str(sample("bus.jpg")))
    a = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
    return Frame(1, 1.0, 1.0, a.shape[1], a.shape[0], array=a, source="test"), img


@pytest.mark.parametrize("yaw, pitch, roll", [(0, 0, 0), (20, 0, 0), (-30, 0, 0), (0, 15, 0), (0, -15, 0),
                                              (0, 0, 10), (25, -10, 5)])
def test_head_pose_round_trip(yaw, pitch, roll):
    W, H = 1280, 720
    rv, _ = cv2.Rodrigues(hp.rotation(yaw, pitch, roll))
    pts, _ = cv2.projectPoints(hp.FACE_3D, rv, np.array([[30.0], [-20.0], [600.0]]), hp.camera_matrix(W, H), None)
    e = hp.estimate(pts.reshape(5, 2), W, H)
    assert e is not None
    assert abs(e.yaw - yaw) < 0.5 and abs(e.pitch - pitch) < 0.5 and abs(e.roll - roll) < 0.5
    assert e.facing == (abs(yaw) < 25 and abs(pitch) < 25)


def test_head_pose_directions_match_the_picture():
    W, H = 1280, 720
    K = hp.camera_matrix(W, H)

    def project(yaw, pitch):
        rv, _ = cv2.Rodrigues(hp.rotation(yaw, pitch, 0))
        return cv2.projectPoints(hp.FACE_3D, rv, np.array([[0.0], [0.0], [600.0]]), K, None)[0].reshape(5, 2)
    p0, right, up = project(0, 0), project(25, 0), project(0, 20)
    nose_dx = lambda p: p[2, 0] - (p[0, 0] + p[1, 0]) / 2          # noqa: E731
    nose_dy = lambda p: p[2, 1] - (p[0, 1] + p[1, 1]) / 2          # noqa: E731
    assert nose_dx(right) > nose_dx(p0) + 5                         # yaw + : the nose moves to the picture's right
    assert nose_dy(up) < nose_dy(p0) - 5                            # pitch + : looking up, nose closer to the eyes
    assert hp.estimate(np.full((5, 2), np.nan), W, H) is None


def test_yunet_keeps_five_landmarks_inside_the_face():
    from vcstudio.ai.onnx_engine import YuNetFaceDetector
    frame, _ = bus_frame()
    faces, _ = YuNetFaceDetector().detect(frame)
    assert faces
    for f in faces:
        assert f.points.shape == (5, 2)
        assert np.all((f.points[:, 0] >= f.x0 - 2) & (f.points[:, 0] <= f.x1 + 2))
        assert np.all((f.points[:, 1] >= f.y0 - 2) & (f.points[:, 1] <= f.y1 + 2))
        assert f.points[0, 1] < f.points[3, 1]                     # eyes above the mouth


@needs_pose
def test_pose_on_a_real_photo():
    _, img = bus_frame()
    poses, ms = pose.PoseEstimator(max_people=2).estimate(img)
    assert poses and ms > 0
    p = poses[0]
    L = p.landmarks
    # the man on the left of bus.jpg: head above shoulders above hips above ankles, all inside his box area
    assert L[0, 1] < L[11, 1] < L[23, 1] < L[27, 1]
    assert 0 < L[0, 0] < 300 and 380 < L[0, 1] < 520 and L[27, 1] > 780
    assert p.visible(11) and p.visible(23)
    post = p.posture()
    assert abs(post["shoulder_tilt"]) < 15 and abs(post["lean"]) < 15
    box = p.upper_body_box()
    assert box is not None and box[1] < L[11, 1] < box[3] + 1


def test_pose_anchor_count_matches_the_model_layout():
    a = pose.anchors()
    assert a.shape == (2254, 2) and a.min() > 0 and a.max() < 1


class Slot:
    def __init__(self, frame):
        self.f = frame

    def peek(self):
        return self.f, id(self.f)


class Cap:
    def __init__(self, frame):
        self.slot = Slot(frame)


class Settings:
    def __init__(self, d):
        self.d = d

    def get(self, key):
        return self.d[key]


def test_vision_service_runs_and_rejects_stale_results():
    from vcstudio.ai.onnx_engine import YuNetFaceDetector
    from vcstudio.ai.vision import STALE_S, VisionService
    frame, _ = bus_frame()
    vs = VisionService(Cap(frame), Settings({"vision": {"enabled": True, "fps": 20, "face": True, "pose": False,
                                                        "hands": False}}),
                       factories={"face": YuNetFaceDetector})
    vs.apply_settings()
    try:
        deadline = time.time() + 10
        while time.time() < deadline and vs.slot.peek()[0] is None:
            time.sleep(0.05)
        r = vs.result_for(frame.pts)
        assert r is not None and r.faces and r.faces[0].pose is not None and "face" in r.ms
        assert vs.result_for(frame.pts + STALE_S + 0.1) is None    # an old result is never drawn
        assert vs.stats.state == "running" and vs.stats.faces == len(r.faces)
        vs.rate_scale = 0.25                                       # the scheduler slows it down
    finally:
        vs.stop()
    assert vs.stats.state == "stopped" and vs.slot.peek()[0] is None


def test_vision_reuses_gesture_hands_instead_of_a_second_detector():
    from vcstudio.ai.vision import VisionConfig, VisionService
    frame, _ = bus_frame()

    class G:
        running = True

        class stats:
            state = "running"
        last_hands = ["hand"]
        last_hands_pts = frame.pts
    made = []
    vs = VisionService(Cap(frame), Settings({"vision": {}}), gestures=G(),
                       factories={"hands": lambda: made.append(1)})
    r = vs.analyse(frame, {}, VisionConfig(enabled=True, face=False, pose=False, hands=True))
    assert r.hands == ["hand"] and not made


def test_vision_without_models_reports_it(monkeypatch):
    from vcstudio.ai.vision import VisionService
    frame, _ = bus_frame()
    vs = VisionService(Cap(frame), Settings({"vision": {"enabled": True, "face": False, "pose": True}}),
                       factories={"pose": lambda: None})
    vs.apply_settings()
    deadline = time.time() + 5
    while time.time() < deadline and vs.stats.state != "no_models":
        time.sleep(0.05)
    vs.stop()
    assert vs.stats.missing == ["pose"]


def test_pipeline_draws_the_overlay(paths):
    from vcstudio.ai.vision import FaceInfo, VisionResult
    from vcstudio.app.application import AppContext
    ctx = AppContext(paths, configure_logging=False)
    try:
        ctx.settings.set("output.camera_enabled", False)
        ctx.settings.set("input.method", "test_pattern")
        ctx.settings.set("input.preferred_width", 640)
        ctx.settings.set("input.preferred_height", 360)
        ctx.vision.cfg = type(ctx.vision.cfg)(enabled=True)        # overlay on, no thread needed
        ctx.start_capture()
        deadline = time.time() + 15
        drawn = None
        while time.time() < deadline:
            f, _ = ctx.capture.slot.peek()
            if f is not None:                                      # a fresh fake result for the newest frame
                pts = np.array([[300, 150], [340, 150], [320, 175], [305, 195], [335, 195]], np.float32)
                ctx.vision.slot.put(VisionResult(f.pts, 640, 360, faces=[FaceInfo((290, 130, 350, 210), pts,
                                                                                  hp.estimate(pts, 640, 360))]))
            pair, _ = ctx.pipeline.processed.peek()
            if pair is not None and "+vision" in pair[1].source:
                drawn = pair
                break
            time.sleep(0.05)
        assert drawn is not None
        with drawn[1].bgra() as a:
            assert tuple(a[150, 300, :3]) == (0, 230, 255)        # a yellow landmark dot
        with drawn[0].bgra() as a:
            assert tuple(a[150, 300, :3]) != (0, 230, 255)        # the input frame itself is untouched
    finally:
        ctx.stop_capture()
        ctx.shutdown()
