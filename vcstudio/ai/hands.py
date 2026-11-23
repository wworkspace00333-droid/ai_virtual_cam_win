"""Hands and gestures (roadmap P2.7): MediaPipe palm detector + 21 hand landmarks (ONNX from the OpenCV Zoo,
Apache-2.0; downloaded on the user's click, vcstudio/ai/model_download.py HANDS), then a rule-based gesture.

  PalmDetector     192×192 letterboxed RGB input, 2016 SSD anchors (24×24×2 + 12×12×6, generated here), boxes and
                   7 palm key points; greedy NMS
  HandLandmarker   crops the hand around the palm, rotated so the hand points up (wrist → middle-finger base),
                   224×224 input; 21 landmarks back in image pixels, hand confidence, handedness. The crop geometry
                   (enlarge ×4 for the rotation, then shift up 0.4 and enlarge ×3) follows the OpenCV Zoo reference
                   (opencv_zoo/models/handpose_estimation_mediapipe, Apache-2.0)
  classify()       open_palm · fist · victory · point · thumbs_up from which fingers are extended

Landmark indices (MediaPipe): 0 wrist; thumb 1-4; index 5-8; middle 9-12; ring 13-16; pinky 17-20.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

PALM_FILE = "hands/palm_detection_mediapipe_2023feb.onnx"
HANDPOSE_FILE = "hands/handpose_estimation_mediapipe_2023feb.onnx"
GESTURES = ("open_palm", "fist", "victory", "point", "thumbs_up")


def models_dir() -> Path:
    """The checkout's / bundle's models folder when it has the hand models, else the app data folder (download)."""
    from vcstudio.ai.open_vocab import user_models_dir
    from vcstudio.core.resources import models_dir as md
    bundled = md()
    return bundled if (bundled / PALM_FILE).exists() else user_models_dir()


def available() -> bool:
    d = models_dir()
    return (d / PALM_FILE).exists() and (d / HANDPOSE_FILE).exists()


def anchors() -> np.ndarray:
    """(2016, 2) anchor centres in [0, 1]: 24×24 grid with 2 anchors per cell, then 12×12 with 6."""
    out = []
    for g, n in ((24, 2), (12, 6)):
        ys, xs = np.mgrid[0:g, 0:g]
        c = np.stack([(xs + 0.5) / g, (ys + 0.5) / g], axis=-1).reshape(-1, 2)
        out.append(np.repeat(c, n, axis=0))
    return np.concatenate(out).astype(np.float32)


def nms(boxes: np.ndarray, scores: np.ndarray, iou: float = 0.3, top: int = 4) -> list[int]:
    order = np.argsort(-scores)
    keep: list[int] = []
    while order.size and len(keep) < top:
        i = int(order[0])
        keep.append(i)
        rest = order[1:]
        x0 = np.maximum(boxes[i, 0], boxes[rest, 0]); y0 = np.maximum(boxes[i, 1], boxes[rest, 1])
        x1 = np.minimum(boxes[i, 2], boxes[rest, 2]); y1 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.clip(x1 - x0, 0, None) * np.clip(y1 - y0, 0, None)
        area = lambda b: (b[..., 2] - b[..., 0]) * (b[..., 3] - b[..., 1])       # noqa: E731
        o = inter / np.maximum(area(boxes[i]) + area(boxes[rest]) - inter, 1e-6)
        order = rest[o <= iou]
    return keep


@dataclass
class Palm:
    box: np.ndarray            # (4,) x0, y0, x1, y1 in image pixels
    keypoints: np.ndarray      # (7, 2): 0 palm base, 2 middle-finger base, ...
    score: float


@dataclass
class Hand:
    landmarks: np.ndarray      # (21, 2) image pixels
    box: tuple[float, float, float, float]
    confidence: float
    right: bool                # handedness as the model sees the (unmirrored) picture
    gesture: str = ""


class PalmDetector:
    SIZE = 192

    def __init__(self, path: Path, compute_units: str = "all", cache_dir: Path | None = None,
                 score: float = 0.5):
        from vcstudio.ai.onnx_engine import create_session
        self.sess, self.backend = create_session(path, compute_units, cache_dir)
        self.inp = self.sess.get_inputs()[0].name
        self.anchors = anchors()
        self.score = score

    def detect(self, bgr: np.ndarray) -> list[Palm]:
        h, w = bgr.shape[:2]
        s = self.SIZE
        ratio = min(s / h, s / w)
        nh, nw = max(1, int(h * ratio)), max(1, int(w * ratio))
        top, left = (s - nh) // 2, (s - nw) // 2
        canvas = np.zeros((s, s, 3), np.uint8)
        canvas[top:top + nh, left:left + nw] = cv2.resize(bgr, (nw, nh), interpolation=cv2.INTER_AREA)
        blob = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB).astype(np.float32)[None] / 255.0
        raw, logit = self.sess.run(None, {self.inp: blob})
        scores = 1.0 / (1.0 + np.exp(-logit[0, :, 0].astype(np.float64)))
        cand = np.where(scores >= self.score)[0]
        if cand.size == 0:
            return []
        d = raw[0, cand]
        a = self.anchors[cand]
        cxy = d[:, :2] / s + a
        wh = d[:, 2:4] / s
        boxes_n = np.concatenate([cxy - wh / 2, cxy + wh / 2], axis=1)       # letterboxed canvas, [0, 1]
        kps_n = d[:, 4:18].reshape(-1, 7, 2) / s + a[:, None, :]

        def to_img(v):                                                         # canvas [0, 1] -> image pixels
            v = v * s
            v[..., 0::2] = (v[..., 0::2] - left) / ratio
            v[..., 1::2] = (v[..., 1::2] - top) / ratio
            return v
        boxes = to_img(boxes_n.copy())
        kps = to_img(kps_n.reshape(-1, 14).copy()).reshape(-1, 7, 2)
        keep = nms(boxes, scores[cand])
        return [Palm(boxes[i], kps[i], float(scores[cand][i])) for i in keep]


class HandLandmarker:
    SIZE = 224

    def __init__(self, path: Path, compute_units: str = "all", cache_dir: Path | None = None,
                 confidence: float = 0.7):
        from vcstudio.ai.onnx_engine import create_session
        self.sess, self.backend = create_session(path, compute_units, cache_dir)
        self.inp = self.sess.get_inputs()[0].name
        self.confidence = confidence

    @staticmethod
    def _square_crop(img: np.ndarray, box: np.ndarray, shift: tuple[float, float], enlarge: float,
                     diagonal: bool) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Shift / enlarge the box ([[x0, y0], [x1, y1]]), crop, pad to a square (side: the diagonal when the crop
        will be rotated). Returns (square image, clipped box, offset of the square's origin in `img`)."""
        wh = box[1] - box[0]
        box = box + np.array(shift) * wh
        c = box.sum(axis=0) / 2
        half = (box[1] - box[0]) * enlarge / 2
        b = np.array([c - half, c + half]).astype(np.int32)
        b[:, 0] = np.clip(b[:, 0], 0, img.shape[1])
        b[:, 1] = np.clip(b[:, 1], 0, img.shape[0])
        crop = img[b[0, 1]:b[1, 1], b[0, 0]:b[1, 0]]
        side = int(np.linalg.norm(crop.shape[:2])) if diagonal else max(crop.shape[:2])
        side = max(side, 1)
        ph, pw = side - crop.shape[0], side - crop.shape[1]
        top, left = ph // 2, pw // 2
        sq = cv2.copyMakeBorder(crop, top, ph - top, left, pw - left, cv2.BORDER_CONSTANT, value=(0, 0, 0))
        return sq, b, b[0] - np.array([left, top])

    def landmarks(self, bgr: np.ndarray, palm: Palm) -> Hand | None:
        # 1. a generous square around the palm, then rotate it so the hand points up
        sq, _, off = self._square_crop(bgr, palm.box.reshape(2, 2), (0.0, 0.0), 4.0, diagonal=True)
        if sq.size == 0:
            return None
        kp = palm.keypoints - off
        p1, p2 = kp[0], kp[2]                                      # palm base -> middle-finger base
        rad = math.pi / 2 - math.atan2(-(p2[1] - p1[1]), p2[0] - p1[0])
        rad = rad - 2 * math.pi * math.floor((rad + math.pi) / (2 * math.pi))
        angle = math.degrees(rad)
        pb = palm.box.reshape(2, 2) - off
        centre = tuple(float(v) for v in pb.sum(axis=0) / 2)
        rot = cv2.getRotationMatrix2D(centre, angle, 1.0)
        rotated = cv2.warpAffine(sq, rot, (sq.shape[1], sq.shape[0]))
        rk = np.c_[kp, np.ones(len(kp))] @ rot.T
        rbox = np.array([rk.min(axis=0), rk.max(axis=0)])
        # 2. the hand crop the landmark model expects
        crop, cbox, _ = self._square_crop(rotated, rbox, (0.0, -0.4), 3.0, diagonal=False)
        if crop.size == 0:
            return None
        blob = cv2.cvtColor(cv2.resize(crop, (self.SIZE, self.SIZE), interpolation=cv2.INTER_AREA),
                            cv2.COLOR_BGR2RGB).astype(np.float32)[None] / 255.0
        lm, conf, handed, _world = self.sess.run(None, {self.inp: blob})
        conf = float(conf.reshape(-1)[0])
        if conf < self.confidence:
            return None
        pts = lm.reshape(-1, 3)[:, :2].astype(np.float64)
        # 3. back to image pixels: model input -> crop -> un-rotate -> square -> image
        scale = float(max((cbox[1] - cbox[0]) / self.SIZE))
        pts = (pts - self.SIZE / 2) * scale
        r2 = cv2.getRotationMatrix2D((0, 0), angle, 1.0)[:, :2]
        pts = pts @ r2                                             # rotate back about the crop centre
        inv = cv2.invertAffineTransform(rot)
        c = np.append(cbox.sum(axis=0) / 2, 1.0)
        pts = pts + inv @ c + off
        x0, y0 = pts.min(axis=0)
        x1, y1 = pts.max(axis=0)
        return Hand(pts, (float(x0), float(y0), float(x1), float(y1)), conf, float(handed.reshape(-1)[0]) > 0.5)


def _d(a, b) -> float:
    return float(np.hypot(*(np.asarray(a) - np.asarray(b))))


def extended_fingers(lm: np.ndarray) -> dict[str, bool]:
    """Which fingers are stretched out. A finger is extended when its tip is clearly farther from the wrist than its
    middle joint; the thumb when its tip is far from the index finger's base compared with the palm's width."""
    wrist = lm[0]
    out = {}
    for name, pip, tip in (("index", 6, 8), ("middle", 10, 12), ("ring", 14, 16), ("pinky", 18, 20)):
        out[name] = _d(lm[tip], wrist) > 1.15 * _d(lm[pip], wrist)
    palm_w = max(_d(lm[5], lm[17]), 1e-6)
    out["thumb"] = _d(lm[4], lm[5]) > 0.75 * palm_w and _d(lm[4], lm[17]) > _d(lm[3], lm[17])
    return out


def classify(lm: np.ndarray) -> str:
    """One of GESTURES, or "" when the hand shape is none of them."""
    e = extended_fingers(lm)
    four = [e["index"], e["middle"], e["ring"], e["pinky"]]
    if all(four):
        return "open_palm"
    if not any(four):
        if e["thumb"]:
            hand = max(_d(lm[0], lm[9]), 1e-6)
            dx, dy = lm[4][0] - lm[2][0], lm[4][1] - lm[2][1]          # thumb base -> tip
            if (lm[4][1] < lm[3][1] < lm[2][1] and lm[0][1] - lm[4][1] > 0.6 * hand
                    and -dy > 2 * abs(dx)):                             # pointing UP, not sideways
                return "thumbs_up"
            return ""
        return "fist"
    if e["index"] and e["middle"] and not e["ring"] and not e["pinky"]:
        return "victory"
    if e["index"] and not e["middle"] and not e["ring"] and not e["pinky"]:
        return "point"
    return ""


class HandTracker:
    """Palm detector + landmarker; hands(bgr) -> list[Hand] with their gesture."""

    def __init__(self, compute_units: str = "all", cache_dir: Path | None = None, max_hands: int = 2,
                 landmarker: Path | None = None):
        d = models_dir()
        self.palms = PalmDetector(d / PALM_FILE, compute_units, cache_dir)
        self.marks = HandLandmarker(landmarker or d / HANDPOSE_FILE, compute_units, cache_dir)   # custom: same layout
        self.backend = self.palms.backend
        self.max_hands = max_hands

    def hands(self, bgr: np.ndarray) -> list[Hand]:
        out = []
        for p in self.palms.detect(bgr)[:self.max_hands]:
            h = self.marks.landmarks(bgr, p)
            if h is not None:
                h.gesture = classify(h.landmarks)
                out.append(h)
        return out
