"""Body pose (0.6.0 Phase 3): MediaPipe person detector + 39-point pose landmarker, ONNX from the OpenCV Zoo
(Apache-2.0; downloaded on the user's click with SHA-256 check, vcstudio/ai/model_download.py POSE).

  PersonDetector  224×224 letterboxed RGB in [-1, 1]; 2254 SSD anchors (28×28×2 + 14×14×2 + 7×7×6); per anchor a box
                  and 4 key points: hip centre, a point on the full-body circle, shoulder centre, a point on the
                  upper-body circle. Greedy NMS.
  PoseLandmarker  the person cropped around the hip centre, rotated upright (hip centre → full-body point), scaled
                  ×1.25 to 256×256 RGB in [0, 1]; 39 landmarks (33 body points + 6 auxiliary) with x, y, z,
                  visibility, presence; landmarks are mapped back to frame pixels. Crop geometry follows the OpenCV
                  Zoo reference (opencv_zoo/models/pose_estimation_mediapipe, Apache-2.0).

Landmark indices (MediaPipe / BlazePose): 0 nose; 11/12 shoulders; 13/14 elbows; 15/16 wrists; 23/24 hips;
25/26 knees; 27/28 ankles; the rest are face, hand and foot points.
Pose is body geometry only: nothing identifies a person.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

PERSON_FILE = "pose/person_detection_mediapipe_2023mar.onnx"
POSE_FILE = "pose/pose_estimation_mediapipe_2023mar.onnx"
DET_SIZE = 224
POSE_SIZE = 256

# skeleton lines for overlays (BlazePose topology, body only)
SKELETON = [(11, 12), (11, 13), (13, 15), (12, 14), (14, 16), (11, 23), (12, 24), (23, 24), (23, 25), (25, 27),
            (24, 26), (26, 28), (27, 31), (28, 32), (0, 11), (0, 12)]
UPPER = (0, 11, 12, 13, 14, 15, 16, 23, 24)


def models_dir() -> Path:
    from vcstudio.ai.open_vocab import user_models_dir
    from vcstudio.core.resources import models_dir as md
    bundled = md()
    return bundled if (bundled / POSE_FILE).exists() else user_models_dir()


def available() -> bool:
    d = models_dir()
    return (d / PERSON_FILE).exists() and (d / POSE_FILE).exists()


def anchors() -> np.ndarray:
    out = []
    for g, n in ((28, 2), (14, 2), (7, 6)):
        ys, xs = np.mgrid[0:g, 0:g]
        c = np.stack([(xs + 0.5) / g, (ys + 0.5) / g], -1).reshape(-1, 1, 2)
        out.append(np.repeat(c, n, axis=1).reshape(-1, 2))
    return np.concatenate(out).astype(np.float32)


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -50, 50)))


@dataclass
class PersonBox:
    score: float
    box: tuple[float, float, float, float]        # x0, y0, x1, y1 in frame pixels
    kps: np.ndarray                               # (4, 2) frame pixels


@dataclass
class Pose:
    score: float
    landmarks: np.ndarray                         # (39, 3) x, y in frame pixels, z (relative)
    visibility: np.ndarray                        # (39,) 0..1
    box: tuple[float, float, float, float]
    extra: dict = field(default_factory=dict)

    def visible(self, i: int, thr: float = 0.5) -> bool:
        return bool(self.visibility[i] >= thr)

    def posture(self) -> dict:
        """Upper-body posture from the shoulders and hips: shoulder tilt (degrees, + = right shoulder lower),
        lean (degrees from vertical of the shoulder-centre → hip-centre line), and which of them are reliable."""
        L = self.landmarks
        out: dict = {"shoulder_tilt": None, "lean": None}
        if self.visible(11) and self.visible(12):
            dx, dy = L[11, 0] - L[12, 0], L[11, 1] - L[12, 1]
            out["shoulder_tilt"] = round(math.degrees(math.atan2(dy, abs(dx) + 1e-6)), 1)
            if self.visible(23) and self.visible(24):
                sc, hc = (L[11, :2] + L[12, :2]) / 2, (L[23, :2] + L[24, :2]) / 2
                out["lean"] = round(math.degrees(math.atan2(sc[0] - hc[0], hc[1] - sc[1] + 1e-6)), 1)
        return out

    def upper_body_box(self, thr: float = 0.5) -> tuple[float, float, float, float] | None:
        pts = [self.landmarks[i, :2] for i in UPPER if self.visible(i, thr)]
        if len(pts) < 3:
            return None
        a = np.array(pts)
        return float(a[:, 0].min()), float(a[:, 1].min()), float(a[:, 0].max()), float(a[:, 1].max())


def _session(path: Path, cache_dir: Path | None = None):
    from vcstudio.ai.onnx_engine import create_session
    sess, _label = create_session(path, "cpu_only", cache_dir)     # small models: the CPU avoids GPU transfers
    return sess


class PersonDetector:
    def __init__(self, cache_dir: Path | None = None, score: float = 0.5, nms: float = 0.3):
        self.sess = _session(models_dir() / PERSON_FILE, cache_dir)
        self.inp = self.sess.get_inputs()[0].name
        self.nchw = self.sess.get_inputs()[0].shape[1] == 3
        self.anchors = anchors()
        self.score, self.nms = score, nms

    def detect(self, bgr: np.ndarray, max_people: int = 2) -> list[PersonBox]:
        h, w = bgr.shape[:2]
        s = DET_SIZE / max(w, h)
        nw, nh = max(1, int(round(w * s))), max(1, int(round(h * s)))
        px, py = (DET_SIZE - nw) // 2, (DET_SIZE - nh) // 2
        canvas = np.zeros((DET_SIZE, DET_SIZE, 3), np.uint8)
        canvas[py:py + nh, px:px + nw] = cv2.resize(bgr, (nw, nh), interpolation=cv2.INTER_AREA)
        x = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB).astype(np.float32) / 127.5 - 1.0
        if self.nchw:
            x = x.transpose(2, 0, 1)
        outs = self.sess.run(None, {self.inp: np.ascontiguousarray(x[None])})
        reg, sc = (outs[0], outs[1]) if outs[0].shape[-1] == 12 else (outs[1], outs[0])
        reg, scores = reg[0], _sigmoid(sc[0, :, 0])
        keep = np.where(scores >= self.score)[0]
        if keep.size == 0:
            return []
        a = self.anchors[keep]
        r = reg[keep] / DET_SIZE
        cx, cy = r[:, 0] + a[:, 0], r[:, 1] + a[:, 1]
        bw, bh = r[:, 2], r[:, 3]
        boxes = np.stack([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], 1)
        kps = r[:, 4:12].reshape(-1, 4, 2) + a[:, None, :]
        order = np.argsort(-scores[keep])
        out: list[PersonBox] = []
        taken: list[np.ndarray] = []
        for i in order:
            b = boxes[i]
            if any(_iou(b, t) > self.nms for t in taken):
                continue
            taken.append(b)

            def back(p):
                return ((p[..., 0] * DET_SIZE - px) / s, (p[..., 1] * DET_SIZE - py) / s)
            bx = back(b.reshape(2, 2))
            kx, ky = back(kps[i])
            out.append(PersonBox(float(scores[keep][i]),
                                 (float(max(0, bx[0][0])), float(max(0, bx[1][0])),
                                  float(min(w, bx[0][1])), float(min(h, bx[1][1]))),
                                 np.stack([kx, ky], 1).astype(np.float32)))
            if len(out) >= max_people:
                break
        return out


def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return float(inter / u) if u > 0 else 0.0


class PoseLandmarker:
    def __init__(self, cache_dir: Path | None = None, score: float = 0.5):
        self.sess = _session(models_dir() / POSE_FILE, cache_dir)
        self.inp = self.sess.get_inputs()[0].name
        self.score = score

    def run(self, bgr: np.ndarray, person: PersonBox) -> Pose | None:
        hip, full = person.kps[0], person.kps[1]
        size = 2.0 * float(np.linalg.norm(full - hip)) * 1.25
        if size < 8:
            return None
        angle = math.pi / 2 - math.atan2(-(full[1] - hip[1]), full[0] - hip[0])   # radians: make the body upright
        k = POSE_SIZE / size
        c, s = math.cos(angle) * k, math.sin(angle) * k
        # frame -> crop: rotate by `angle` around the hip centre, scale, centre in the crop
        M = np.array([[c, s, POSE_SIZE / 2 - c * hip[0] - s * hip[1]],
                      [-s, c, POSE_SIZE / 2 + s * hip[0] - c * hip[1]]], np.float32)
        crop = cv2.warpAffine(bgr, M, (POSE_SIZE, POSE_SIZE), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0))
        x = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        outs = self.sess.run(None, {self.inp: x[None]})
        named = {o.name: v for o, v in zip(self.sess.get_outputs(), outs)}
        lm = next(v for v in named.values() if v.shape[-1] == 195).reshape(39, 5)
        conf = float(np.ravel(next(v for v in named.values() if v.size == 1))[0])
        conf = conf if 0.0 <= conf <= 1.0 else float(_sigmoid(conf))
        if conf < self.score:
            return None
        Mi = cv2.invertAffineTransform(M)
        pts = np.concatenate([lm[:, :2], np.ones((39, 1), np.float32)], 1) @ Mi.T
        z = lm[:, 2:3] / k
        vis = _sigmoid(lm[:, 3])
        return Pose(conf, np.concatenate([pts, z], 1).astype(np.float32), vis.astype(np.float32), person.box)


class PoseEstimator:
    """Person detector + landmarker; `estimate(bgr)` -> (poses, ms)."""

    def __init__(self, cache_dir: Path | None = None, max_people: int = 2):
        self.det = PersonDetector(cache_dir)
        self.lm = PoseLandmarker(cache_dir)
        self.max_people = max_people

    def estimate(self, bgr: np.ndarray) -> tuple[list[Pose], float]:
        t0 = time.perf_counter()
        poses = []
        for p in self.det.detect(bgr, self.max_people):
            r = self.lm.run(bgr, p)
            if r is not None:
                poses.append(r)
        return poses, (time.perf_counter() - t0) * 1000


def draw(bgra: np.ndarray, poses: list[Pose], thr: float = 0.5) -> None:
    """Skeleton overlay in place (cyan bones, white joints)."""
    for p in poses:
        L = p.landmarks
        for a, b in SKELETON:
            if p.visible(a, thr) and p.visible(b, thr):
                cv2.line(bgra, (int(L[a, 0]), int(L[a, 1])), (int(L[b, 0]), int(L[b, 1])), (255, 220, 0, 255), 2,
                         cv2.LINE_AA)
        for i in set(j for e in SKELETON for j in e):
            if p.visible(i, thr):
                cv2.circle(bgra, (int(L[i, 0]), int(L[i, 1])), 3, (255, 255, 255, 255), -1, cv2.LINE_AA)
