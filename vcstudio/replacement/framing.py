"""Auto-framing: the camera crops to the people in view and follows them smoothly (profile section "framing").

  target   the union of tracked people (or faces, if no person is tracked), padded by `margin` of its size, widened to
           the output's aspect ratio, never smaller than frame / `zoom_max`, kept inside the frame
  motion   critically damped: the crop eases towards the target with time constant `tau` seconds (frame-rate
           independent); movements inside the dead zone (a small share of the crop) are ignored, so a person
           shifting in their seat does not make the picture swim
  nobody   after `lost_s` seconds without a person the crop eases back to the full frame

It is the last step of the output: regions and replacements are drawn in full-frame coordinates first, then the
whole picture is cropped and scaled back to the output size (one resize). Detection must run for it, so a profile
with framing on counts as needing the detector (vcstudio/ai/detection.py).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

TARGET_CLASSES = ("person",)
FALLBACK_CLASSES = ("face",)


@dataclass
class FramingConfig:
    enabled: bool = False
    zoom_max: float = 2.0
    margin: float = 0.25
    tau: float = 0.6
    deadzone: float = 0.04
    lost_s: float = 2.0
    locked_only: bool = False     # 0.6.0: frame only the people locked in the tracker (Studio → AI Vision)

    @classmethod
    def from_profile(cls, body: dict) -> "FramingConfig":
        f = body.get("framing") or {}
        return cls(enabled=bool(f.get("enabled", False)),
                   zoom_max=float(min(4.0, max(1.0, f.get("zoom_max", 2.0)))),
                   margin=float(min(1.0, max(0.0, f.get("margin", 0.25)))),
                   tau=float(min(5.0, max(0.05, f.get("tau", 0.6)))),
                   locked_only=bool(f.get("locked_only", False)))


class AutoFramer:
    def __init__(self, cfg: FramingConfig | None = None):
        self.cfg = cfg or FramingConfig()
        self.rect: tuple[float, float, float, float] | None = None   # current crop (x, y, w, h), frame pixels
        self._t: float | None = None
        self._seen: float | None = None
        self._size: tuple[int, int] | None = None
        self._moving = False

    def configure(self, cfg: FramingConfig) -> None:
        if not cfg.enabled:
            self.reset()
        self.cfg = cfg

    def reset(self) -> None:
        self.rect, self._t, self._seen, self._size = None, None, None, None
        self._moving = False

    # ---------------- target
    def target(self, tracked, W: int, H: int) -> tuple[float, float, float, float] | None:
        people = [o for o in tracked if o.label in TARGET_CLASSES]
        if self.cfg.locked_only:                     # frame the chosen person(s); nobody locked: everyone as usual
            locked = [o for o in people if getattr(o, "locked", False)]
            people = locked or people
        boxes = [(o.x0, o.y0, o.x1, o.y1) for o in people]
        if not boxes:
            boxes = [(o.x0, o.y0, o.x1, o.y1) for o in tracked if o.label in FALLBACK_CLASSES]
            if boxes:                                    # a face alone: frame head and shoulders
                boxes = [(x0 - (x1 - x0), y0 - 0.5 * (y1 - y0), x1 + (x1 - x0), y1 + 2.0 * (y1 - y0))
                         for x0, y0, x1, y1 in boxes]
        if not boxes:
            return None
        x0 = min(b[0] for b in boxes); y0 = min(b[1] for b in boxes)
        x1 = max(b[2] for b in boxes); y1 = max(b[3] for b in boxes)
        bw, bh = max(1.0, x1 - x0), max(1.0, y1 - y0)
        m = self.cfg.margin
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        w, h = bw * (1 + 2 * m), bh * (1 + 2 * m)
        aspect = W / H
        if w / h < aspect:
            w = h * aspect
        else:
            h = w / aspect
        min_w = W / self.cfg.zoom_max
        if w < min_w:
            w, h = min_w, min_w / aspect
        if w > W:
            w, h = float(W), float(H)
        x = min(max(0.0, cx - w / 2), W - w)
        y = min(max(0.0, cy - h / 2), H - h)
        return x, y, w, h

    # ---------------- motion
    def update(self, tracked, W: int, H: int, t: float) -> tuple[float, float, float, float]:
        full = (0.0, 0.0, float(W), float(H))
        if self._size != (W, H):
            self.reset()
            self._size = (W, H)
        want = self.target(tracked, W, H)
        if want is not None:
            self._seen = t
        elif self._seen is None or t - self._seen > self.cfg.lost_s:
            want = full
        else:
            want = self.rect or full                     # briefly lost: hold the current framing
        if self.rect is None or self._t is None:
            self.rect, self._t = (want if want is not None else full), t
            return self.rect
        dt = max(0.0, min(0.5, t - self._t))
        self._t = t
        x, y, w, h = self.rect
        tx, ty, tw, th = want
        dcx, dcy, dw = abs((tx + tw / 2) - (x + w / 2)), abs((ty + th / 2) - (y + h / 2)), abs(tw - w)
        # Hysteresis: the dead zone only decides whether to START moving (small shifts are ignored); once moving,
        # the crop goes all the way to the target. Without it the last stretch of a zoom-out stayed inside the dead
        # zone and the picture stopped slightly zoomed in.
        if not self._moving:
            dz = self.cfg.deadzone * w
            if dcx < dz and dcy < dz and dw < 2 * dz:
                return self.rect                         # inside the dead zone: keep still
            self._moving = True
        if dcx < 0.5 and dcy < 0.5 and dw < 0.5:         # arrived
            self.rect, self._moving = (tx, ty, tw, th), False
            return self.rect
        k = 1.0 - math.exp(-dt / self.cfg.tau)
        nw = w + (tw - w) * k
        nh = nw * H / W
        ncx = (x + w / 2) + ((tx + tw / 2) - (x + w / 2)) * k
        ncy = (y + h / 2) + ((ty + th / 2) - (y + h / 2)) * k
        nx = min(max(0.0, ncx - nw / 2), W - nw)
        ny = min(max(0.0, ncy - nh / 2), H - nh)
        self.rect = (nx, ny, nw, nh)
        return self.rect

    def apply(self, img: np.ndarray, rect) -> np.ndarray:
        """Crop `rect` from a BGRA frame and scale it back to the frame size (a full-frame rect: the frame itself)."""
        H, W = img.shape[:2]
        x, y, w, h = rect
        if w >= W - 0.5 and h >= H - 0.5:
            return img
        x0, y0 = int(round(x)), int(round(y))
        x1, y1 = min(W, int(round(x + w))), min(H, int(round(y + h)))
        return cv2.resize(img[y0:y1, x0:x1], (W, H), interpolation=cv2.INTER_LINEAR)
