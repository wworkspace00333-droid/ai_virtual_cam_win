"""Dense face mesh (0.6.0): 478 points per face (468 face + 10 iris), MediaPipe Face Landmarker v2 topology.

Model: an ONNX export (tf2onnx) of MediaPipe's face landmark model, published on Hugging Face by astaileyyoung
(licence field MIT; MediaPipe itself is Apache-2.0); downloaded on the user's click with SHA-256 check
(vcstudio/ai/model_download.py FACE_MESH). The repository does not document the conversion, so tests check the mesh
against YuNet's landmarks on real photos before it is relied on.

  input   256×256 RGB in [0, 1] (NHWC): the face from YuNet, a square crop with 25 % margin on each side, rotated so
          the eyes are level (YuNet's eye points), as MediaPipe crops it
  output  1434 values = 478 × (x, y, z) in crop pixels, and a face-presence logit
Points are mapped back to frame pixels. Index reminders (MediaPipe): 1 nose tip, 33 / 263 outer eye corners,
61 / 291 mouth corners, 152 chin, 10 forehead, 468-472 / 473-477 the irises.
Geometry only: the mesh says where the face is, never who it is.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

MESH_FILE = "facemesh/face_mesh_468.onnx"
SIZE = 256
MARGIN = 0.25
# a few landmark indices used by overlays and checks
NOSE_TIP, LEFT_EYE_OUTER, RIGHT_EYE_OUTER, MOUTH_LEFT, MOUTH_RIGHT, CHIN, FOREHEAD = 1, 33, 263, 61, 291, 152, 10
FACE_OVAL = [10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288, 397, 365, 379, 378, 400, 377, 152, 148, 176,
             149, 150, 136, 172, 58, 132, 93, 234, 127, 162, 21, 54, 103, 67, 109]


def models_dir() -> Path:
    from vcstudio.ai.open_vocab import user_models_dir
    from vcstudio.core.resources import models_dir as md
    bundled = md()
    return bundled if (bundled / MESH_FILE).exists() else user_models_dir()


def available() -> bool:
    return (models_dir() / MESH_FILE).exists()


@dataclass
class Mesh:
    points: np.ndarray             # (478, 3) frame pixels (x, y) and depth z (crop pixels, relative)
    score: float

    def oval(self) -> np.ndarray:
        """(36, 2) int32 face outline (for a precise privacy mask)."""
        return self.points[FACE_OVAL, :2].astype(np.int32)


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-50.0, min(50.0, x))))


class FaceMesh:
    # The presence logit measured on tests/data/bus.jpg: open face +8.1, face with sunglasses -2.8, a wall -19.9.
    # The face is already confirmed by YuNet, so the bar is low (logit -5): sunglasses pass, non-faces do not.
    def __init__(self, cache_dir: Path | None = None, score: float = 0.0067, path: Path | None = None):
        from vcstudio.ai.onnx_engine import create_session
        self.sess, _ = create_session(Path(path) if path is not None else models_dir() / MESH_FILE, "cpu_only",
                                      cache_dir)          # path: a checked custom model of the same layout
        self.inp = self.sess.get_inputs()[0].name
        self.score = score
        self.ms = 0.0

    @staticmethod
    def crop_transform(box, eyes=None) -> np.ndarray:
        """2x3 affine frame -> crop: square around the box with MARGIN, level eyes when given ((2, 2) points)."""
        x0, y0, x1, y1 = box
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        side = max(x1 - x0, y1 - y0) * (1 + 2 * MARGIN)
        angle = 0.0
        if eyes is not None:
            (ex0, ey0), (ex1, ey1) = eyes
            angle = math.atan2(ey1 - ey0, ex1 - ex0)
        k = SIZE / max(side, 1.0)
        c, s = math.cos(-angle) * k, math.sin(-angle) * k
        return np.array([[c, -s, SIZE / 2 - c * cx + s * cy],
                         [s, c, SIZE / 2 - s * cx - c * cy]], np.float32)

    def run(self, bgr: np.ndarray, box, points5=None) -> Mesh | None:
        """Mesh for one face (YuNet box; its 5 points level the crop). None when the model says no face."""
        t0 = time.perf_counter()
        eyes = None
        if points5 is not None:
            p = np.asarray(points5, np.float32).reshape(5, 2)
            eyes = (p[0], p[1]) if p[0, 0] <= p[1, 0] else (p[1], p[0])
        M = self.crop_transform(box, eyes)
        crop = cv2.warpAffine(bgr, M, (SIZE, SIZE), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        x = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB).astype(np.float32)[None] / 255.0
        outs = self.sess.run(None, {self.inp: x})
        lm = next(o for o in outs if o.size == 1434).reshape(478, 3)
        flag = next((o for o in outs if o.size == 1), None)                  # Identity_1: face presence logit
        score = _sigmoid(float(np.ravel(flag)[0])) if flag is not None else 1.0
        self.ms = (time.perf_counter() - t0) * 1000
        if score < self.score:
            return None
        Mi = cv2.invertAffineTransform(M)
        xy = np.concatenate([lm[:, :2], np.ones((478, 1), np.float32)], 1) @ Mi.T
        return Mesh(np.concatenate([xy, lm[:, 2:3]], 1).astype(np.float32), score)


def draw(bgra: np.ndarray, mesh: Mesh) -> None:
    """Mesh points (small dots) and the face outline."""
    for x, y in mesh.points[:468, :2]:
        bgra[int(min(max(y, 0), bgra.shape[0] - 1)), int(min(max(x, 0), bgra.shape[1] - 1))] = (255, 200, 120, 255)
    cv2.polylines(bgra, [mesh.oval()], True, (255, 200, 120, 255), 1, cv2.LINE_AA)
