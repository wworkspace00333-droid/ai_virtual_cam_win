"""Temporal mask propagation (0.7.0): object silhouettes carried from frame to frame by dense optical flow.

SAM 2.1's own video propagation needs its memory-attention modules, which the ONNX export does not have; running
the image model on every detection costs seconds per frame on a CPU. So SAM (or any box segmenter) runs on key
frames only, and in between each object's last mask is moved to the new frame with DIS optical flow (OpenCV,
CPU): the flow from the new picture back to the old one, computed on a grey crop around the object, tells where
every mask pixel came from, so the silhouette follows the object's deformation (an arm moving, a head turning), not
only its box. This is a technically valid substitute for memory-based propagation, not SAM 2.1 video tracking.

Identity: a detection continues the remembered object of the same class whose box overlaps it most (IoU >=
MATCH_IOU); the tracker (vcstudio/ai/tracking.py) keeps the track id on top of that. A new key frame is due when
`keyframe_s` has passed, when a wanted object has no remembered mask (new object, or reappeared after occlusion),
or when propagation lost confidence: the warped mask grew or shrank by more than AREA_JUMP, the flow's
forward-backward check failed on more than BAD_FLOW of the mask, or the old picture moved by the flow no longer matches
the new one inside the mask (normalised correlation under MIN_NCC: the object was covered, left, or moved too
fast; the flow always finds *some* match, so the raw difference alone does not tell).
Remembered objects not seen for MAX_AGE_S are forgotten (bounded memory).
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

MATCH_IOU = 0.3
AREA_JUMP = 0.5
BAD_FLOW = 0.35
MIN_NCC = 0.6
MAX_AGE_S = 3.0
PAD = 0.15                 # crop margin around the old and new box, share of the box size
WORK_SIDE = 192            # long side the flow is computed at (DIS on a small crop: a few ms)


@dataclass
class Remembered:
    label: str
    box: tuple[float, float, float, float]
    mask: np.ndarray        # uint8 box-local silhouette (Detection.mask format)
    gray: np.ndarray        # the frame (grey, full size) the mask belongs to
    t: float


def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0.0


def _paste(mask: np.ndarray, box, rect) -> np.ndarray:
    """Box-local mask -> float32 0..1 canvas of `rect` (x0, y0, x1, y1 integer frame pixels)."""
    rx0, ry0, rx1, ry1 = rect
    canvas = np.zeros((ry1 - ry0, rx1 - rx0), np.float32)
    x0, y0, x1, y1 = box
    bw, bh = max(1.0, x1 - x0), max(1.0, y1 - y0)
    mh, mw = mask.shape[:2]
    M = np.array([[mw / bw, 0, (rx0 - x0) * mw / bw], [0, mh / bh, (ry0 - y0) * mh / bh]], np.float32)
    cv2.warpAffine(mask.astype(np.float32) / 255.0, M, (rx1 - rx0, ry1 - ry0), dst=canvas,
                   flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    return canvas


def _crop_to_box(canvas: np.ndarray, rect, box, like: np.ndarray) -> np.ndarray:
    """float canvas of `rect` -> uint8 box-local mask of `box`, at the resolution of `like` scaled to the box."""
    rx0, ry0 = rect[0], rect[1]
    x0, y0, x1, y1 = box
    bw, bh = max(1.0, x1 - x0), max(1.0, y1 - y0)
    from vcstudio.ai.segmentation import MASK_MAX
    s = min(1.0, MASK_MAX / max(bw, bh))
    ow, oh = max(2, int(round(bw * s))), max(2, int(round(bh * s)))
    M = np.array([[bw / ow, 0, x0 - rx0], [0, bh / oh, y0 - ry0]], np.float32)
    out = cv2.warpAffine(canvas, M, (ow, oh), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                         borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    return np.clip(out * 255 + 0.5, 0, 255).astype(np.uint8)


class MaskPropagator:
    def __init__(self, keyframe_s: float = 1.0):
        self.keyframe_s = keyframe_s
        self.memory: list[Remembered] = []
        self.last_key = -1e9
        self._dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_FAST)
        self.propagated = 0          # masks carried in the last call (stats / tests)
        self.failed = 0
        self.force_key = False
        self.last_ncc = 1.0

    def _match(self, label: str, box) -> Remembered | None:
        best, bi = None, MATCH_IOU
        for r in self.memory:
            if r.label == label:
                v = _iou(r.box, box)
                if v >= bi:
                    best, bi = r, v
        return best

    def due(self, dets, t: float) -> bool:
        """A key frame is needed for these detections (the segmenter must run)."""
        if self.force_key or t - self.last_key >= self.keyframe_s:
            return True
        return any(self._match(d.label, (d.x0, d.y0, d.x1, d.y1)) is None for d in dets)

    def remember(self, gray: np.ndarray, dets, t: float, key: bool) -> None:
        """Store the masks of this frame (key frame: all are fresh; also called after propagation)."""
        self.memory = [r for r in self.memory if t - r.t <= MAX_AGE_S]
        for d in dets:
            if d.mask is None:
                continue
            box = (d.x0, d.y0, d.x1, d.y1)
            old = self._match(d.label, box)
            if old is not None:
                self.memory.remove(old)
            self.memory.append(Remembered(d.label, box, d.mask, gray, t))
        if key:
            self.last_key = t
            self.force_key = False

    def carry(self, gray: np.ndarray, det, t: float) -> np.ndarray | None:
        """The remembered mask of `det`'s object moved to this frame, or None (no memory / not trustworthy)."""
        box = (det.x0, det.y0, det.x1, det.y1)
        r = self._match(det.label, box)
        if r is None or r.gray.shape != gray.shape:
            return None
        H, W = gray.shape
        ux0, uy0 = min(r.box[0], box[0]), min(r.box[1], box[1])
        ux1, uy1 = max(r.box[2], box[2]), max(r.box[3], box[3])
        px, py = (ux1 - ux0) * PAD, (uy1 - uy0) * PAD
        rect = (max(0, int(ux0 - px)), max(0, int(uy0 - py)), min(W, int(np.ceil(ux1 + px))),
                min(H, int(np.ceil(uy1 + py))))
        rw, rh = rect[2] - rect[0], rect[3] - rect[1]
        if rw < 8 or rh < 8:
            return None
        s = min(1.0, WORK_SIDE / max(rw, rh))
        sw, sh = max(8, int(rw * s)), max(8, int(rh * s))
        prev = cv2.resize(r.gray[rect[1]:rect[3], rect[0]:rect[2]], (sw, sh), interpolation=cv2.INTER_AREA)
        cur = cv2.resize(gray[rect[1]:rect[3], rect[0]:rect[2]], (sw, sh), interpolation=cv2.INTER_AREA)
        back = self._dis.calc(cur, prev, None)                 # where each new pixel was in the old frame
        fwd = self._dis.calc(prev, cur, None)
        old_small = cv2.resize(_paste(r.mask, r.box, rect), (sw, sh), interpolation=cv2.INTER_LINEAR)
        yy, xx = np.mgrid[0:sh, 0:sw].astype(np.float32)
        mx, my = xx + back[..., 0], yy + back[..., 1]
        new_small = cv2.remap(old_small, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        # forward-backward consistency on the moved mask: a covered or too fast object fails here
        fx = cv2.remap(fwd[..., 0], mx, my, cv2.INTER_LINEAR)
        fy = cv2.remap(fwd[..., 1], mx, my, cv2.INTER_LINEAR)
        err = np.hypot(back[..., 0] + fx, back[..., 1] + fy)
        inside = new_small > 0.5
        a_old, a_new = float((old_small > 0.5).sum()), float(inside.sum())
        if a_old < 4 or a_new < 4:
            return None
        bad = float((err[inside] > 1.5).mean())
        moved = cv2.remap(prev, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        a = moved.astype(np.float32)[inside]
        b = cur.astype(np.float32)[inside]
        a, b = a - a.mean(), b - b.mean()
        ncc = float((a * b).sum() / max(1e-6, np.sqrt((a * a).sum() * (b * b).sum())))
        self.last_ncc = ncc
        if abs(a_new - a_old) / a_old > AREA_JUMP or bad > BAD_FLOW or ncc < MIN_NCC:
            self.failed += 1
            self.force_key = True
            return None
        canvas = cv2.resize(new_small, (rw, rh), interpolation=cv2.INTER_LINEAR)
        return _crop_to_box(canvas, rect, box, r.mask)

    def reset(self) -> None:
        self.memory = []
        self.last_key = -1e9
