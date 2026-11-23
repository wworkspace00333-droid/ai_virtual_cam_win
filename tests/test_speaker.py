"""0.6.0 Phase 2: active-speaker camera switching by mouth movement (vcstudio/ai/speaker.py + multicam auto)."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from vcstudio.ai import face_mesh
from vcstudio.ai import speaker as sp
from vcstudio.ai.onnx_engine import yunet_path
from vcstudio.core.frame import Frame
from vcstudio.core.multicam import AutoSwitcher, MulticamConfig

needs_faces = pytest.mark.skipif(not (yunet_path().exists() and face_mesh.available()), reason="face models missing")


def test_mouth_opening_is_lip_gap_over_face_height():
    p = np.zeros((478, 3), np.float32)
    p[10], p[152] = (100, 0, 0), (100, 200, 0)
    p[13], p[14] = (100, 150, 0), (100, 160, 0)
    assert sp.mouth_open(p) == pytest.approx(0.05)
    assert sp.mouth_open(np.zeros((478, 3))) == 0.0


def test_activity_needs_enough_samples_and_forgets_old_ones():
    tr = sp.MouthTrack()
    for i in range(4):
        tr.add(i * 0.1, 0.02 if i % 2 else 0.07)
    assert tr.activity(0.3) == 0.0                                  # 4 < MIN_SAMPLES
    tr.add(0.4, 0.07)
    assert tr.activity(0.4) > sp.THRESHOLD                          # talking
    for i in range(5, 30):
        tr.add(i * 0.1, None)                                       # the face is gone
    assert tr.activity(3.0) == 0.0 and not tr.samples


@pytest.mark.parametrize("act, who", [
    ({}, None),
    ({"main": 0.002, "cam1": 0.004}, None),                        # nobody speaks
    ({"main": 0.002, "cam1": 0.03}, "cam1"),
    ({"main": 0.025, "cam1": 0.03}, None),                         # two at once: no clear speaker
    ({"main": 0.03}, "main"),
])
def test_pick_speaker(act, who):
    assert sp.pick_speaker(act) == who


def test_switcher_follows_the_speaker_with_hold_and_cooldown():
    cfg = MulticamConfig(enabled=True, layout="single", auto="speaker", hold_s=1.0, cooldown_s=5.0,
                         sources=({"id": "cam1", "name": "cam1", "enabled": True, "method": "test"},))
    a = AutoSwitcher()
    assert a.update(cfg, 1, 0.0, "cam1") is None                    # must lead for hold_s first
    assert a.update(cfg, 1, 0.5, "cam1") is None
    assert a.update(cfg, 1, 1.0, "cam1") == "cam1" and a.switches == 1
    assert a.update(cfg, 1, 2.0, None) == "cam1"                    # silence: stay
    assert a.update(cfg, 1, 3.0, "main") == "cam1"                  # cooldown (5 s after the switch at 1.0)
    assert a.update(cfg, 1, 5.9, "main") == "cam1"
    assert a.update(cfg, 1, 6.1, "main") is None and a.switches == 2   # back to the layout's primary
    assert a.update(cfg, 1, 7.0, "main") is None                    # speaker already shown: nothing to do


def test_multicam_service_uses_the_speaker_service():
    from vcstudio.core.multicam import MulticamService

    class Sources:
        def latest(self, sid, now, cfg):
            return np.full((90, 160, 4), 200, np.uint8)

        def sync(self, specs):
            pass

        def stop(self):
            pass

    class Spk:
        started = 0
        stopped = 0

        def speaker(self):
            return "cam1"

        def start(self):
            Spk.started += 1

        def stop(self):
            Spk.stopped += 1

    cfg = MulticamConfig(enabled=True, layout="single", auto="speaker", hold_s=0.5, cooldown_s=0,
                         sources=({"id": "cam1", "name": "cam1", "enabled": True, "method": "test"},))
    mc = MulticamService(Sources())
    mc.speaker = Spk()
    mc.apply(cfg, True)
    assert Spk.started == 1
    out = np.zeros((90, 160, 4), np.uint8)
    mc.process(out, cfg, 1, now=0.0)
    res = mc.process(out, cfg, 1, now=0.6)
    assert mc.stats.primary == "cam1" and res[45, 80, 0] == 200
    mc.apply(MulticamConfig(**{**cfg.__dict__, "auto": "off"}), True)
    assert Spk.stopped >= 1


def _face_crop():
    """A 640x640 close-up of the largest face of bus.jpg and its lip corners (via the real models)."""
    from vcstudio.ai.onnx_engine import YuNetFaceDetector
    img = cv2.imread(str(Path(__file__).parent / "data" / "bus.jpg"))
    a = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
    faces, _ = YuNetFaceDetector().detect(Frame(1, 1.0, 1.0, a.shape[1], a.shape[0], array=a, source="t"))
    f = max(faces, key=lambda d: d.x1 - d.x0)
    cx, cy, side = (f.x0 + f.x1) / 2, (f.y0 + f.y1) / 2, (f.x1 - f.x0) * 3
    x0, y0 = int(max(0, cx - side / 2)), int(max(0, cy - side / 2))
    crop = cv2.resize(img[y0:int(y0 + side), x0:int(x0 + side)], (640, 640), interpolation=cv2.INTER_CUBIC)
    return crop


def _frame(bgr, seq):
    a = cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA)
    return Frame(seq, 0.0, 0.0, a.shape[1], a.shape[0], array=a, source="t")


@needs_faces
def test_real_models_pick_the_camera_whose_mouth_moves():
    """Two sources show the same close-up; on "cam1" the mouth opens and closes (a dark mouth drawn between the
    lip corners the mesh found), on "main" it stays still. The real detector and mesh must name "cam1"."""
    from vcstudio.ai.onnx_engine import YuNetFaceDetector
    crop = _face_crop()
    det, mesh = YuNetFaceDetector(), face_mesh.FaceMesh()
    svc = sp.SpeakerService(lambda: {})
    base = svc.measure(_frame(crop, 0), det, mesh)
    assert base is not None and base < 0.04                       # closed mouth
    m = mesh.run(crop, (213, 213, 427, 427))                       # the face fills the middle third
    assert m is not None
    left, right = m.points[61, :2], m.points[291, :2]
    c = tuple(int(v) for v in (left + right) / 2)
    w = int(np.linalg.norm(right - left) / 2)
    talking = []
    for k in (0.0, 0.6, 0.1, 0.7, 0.0, 0.5, 0.2, 0.6, 0.0, 0.7):
        im = crop.copy()
        if k:
            cv2.ellipse(im, c, (int(w * 0.85), max(1, int(k * w * 0.8))), 0, 0, 360, (30, 20, 40), -1)
        talking.append(im)
    seq = {"n": 0}

    def frames():
        i = seq["n"]
        seq["n"] += 1
        return {"main": _frame(crop, i + 1), "cam1": _frame(talking[i % len(talking)], i + 1)}
    svc.frames = frames
    for i in range(10):
        svc.step(det, mesh, i / 6)
    assert svc.stats.activity["cam1"] > sp.THRESHOLD > svc.stats.activity["main"], svc.stats.activity
    assert svc.stats.speaker == "cam1"


def test_app_wires_the_speaker_service(paths):
    from vcstudio.app.application import AppContext
    ctx = AppContext(paths, configure_logging=False)
    try:
        assert isinstance(ctx.multicam.speaker, sp.SpeakerService)
        fr = ctx.multicam.speaker.frames()
        assert "main" in fr
        body = {"multicam": {"enabled": True, "auto": "speaker"}}
        assert MulticamConfig.from_profile(body).auto == "speaker"
    finally:
        ctx.shutdown()
