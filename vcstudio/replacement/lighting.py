"""Lighting enhancement: automatic exposure and white balance for a webcam picture (profile section "lighting").

  metering        the inner part of the faces in view, or — without a face — a centre-weighted average of the frame,
                  so a bright window behind a person does not make the person darker
  exposure        a gain that brings the metered brightness towards `target` (clamped to [0.7, 2.2]), with a gentle
                  tone curve so highlights are compressed instead of clipped
  white balance   grey-world on the whole frame, each channel gain clamped to [0.8, 1.25] and scaled by `strength`
  smoothing       gains ease with time constant `tau` seconds: a person walking past does not make the picture flicker

The measurement runs on a 1/8-size copy and the correction is three 256-entry lookup tables (cv2.LUT), a few
milliseconds per 1080p frame on a CPU. It is applied to the camera picture BEFORE the background and the
replacements, so the person is corrected and the user's images stay as they are.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class LightingConfig:
    enabled: bool = False
    strength: float = 0.8          # 0 = no change, 1 = full correction
    target: float = 0.48           # metered brightness to aim for (0..1)
    white_balance: bool = True
    tau: float = 0.8

    @classmethod
    def from_profile(cls, body: dict) -> "LightingConfig":
        lc = body.get("lighting") or {}
        return cls(enabled=bool(lc.get("enabled", False)),
                   strength=float(min(1.0, max(0.0, lc.get("strength", 0.8)))),
                   target=float(min(0.7, max(0.3, lc.get("target", 0.48)))),
                   white_balance=bool(lc.get("white_balance", True)),
                   tau=float(min(5.0, max(0.05, lc.get("tau", 0.8)))))


def luma(bgr: np.ndarray) -> np.ndarray:
    return (0.114 * bgr[..., 0] + 0.587 * bgr[..., 1] + 0.299 * bgr[..., 2]) / 255.0


class AutoLight:
    def __init__(self, cfg: LightingConfig | None = None):
        self.cfg = cfg or LightingConfig()
        self.gains = np.ones(3, np.float32)      # current per-channel gains (B, G, R)
        self.metered = 0.0
        self._t: float | None = None
        self._lut_key: tuple | None = None
        self._lut: np.ndarray | None = None

    def reset(self) -> None:
        self.gains = np.ones(3, np.float32)
        self._t = None

    def measure(self, bgra: np.ndarray, faces: list[tuple[float, float, float, float]]) -> np.ndarray:
        """Wanted per-channel gains for this picture (before smoothing)."""
        H, W = bgra.shape[:2]
        s = 8
        small = cv2.resize(bgra[..., :3], (max(1, W // s), max(1, H // s)), interpolation=cv2.INTER_AREA)
        sh, sw = small.shape[:2]
        y = luma(small.astype(np.float32))
        if faces:
            m = np.zeros((sh, sw), bool)
            for x0, y0, x1, y1 in faces:
                # meter INSIDE the face: a detector box is already tight, and any padding around a small face is
                # mostly background (a bright window behind would make the face darker instead of lighter)
                pw, ph = 0.15 * (x1 - x0), 0.15 * (y1 - y0)
                a0, b0 = max(0, int((x0 + pw) / s)), max(0, int((y0 + ph) / s))
                a1, b1 = min(sw, int(math.ceil((x1 - pw) / s))), min(sh, int(math.ceil((y1 - ph) / s)))
                m[b0:max(b0 + 1, b1), a0:max(a0 + 1, a1)] = True
            metered = float(y[m].mean()) if m.any() else float(y.mean())
        else:                                     # centre-weighted: a Gaussian bump over the middle of the frame
            yy, xx = np.mgrid[0:sh, 0:sw]
            wgt = np.exp(-(((xx - sw / 2) / (0.35 * sw)) ** 2 + ((yy - sh / 2) / (0.35 * sh)) ** 2))
            metered = float((y * wgt).sum() / wgt.sum())
        self.metered = metered
        exp_gain = min(2.2, max(0.7, self.cfg.target / max(metered, 1e-3)))
        gains = np.full(3, exp_gain, np.float32)
        if self.cfg.white_balance:
            means = small.reshape(-1, 3).astype(np.float32).mean(axis=0) + 1e-3
            wb = np.clip(means.mean() / means, 0.8, 1.25)
            gains *= wb.astype(np.float32)
        k = self.cfg.strength                      # blend towards "no change"
        return (1.0 + (gains - 1.0) * k).astype(np.float32)

    def update(self, bgra: np.ndarray, faces, t: float) -> np.ndarray:
        want = self.measure(bgra, faces)
        if self._t is None:
            self.gains, self._t = want, t
            return self.gains
        dt = max(0.0, min(0.5, t - self._t))
        self._t = t
        a = 1.0 - math.exp(-dt / self.cfg.tau)
        self.gains = (self.gains + (want - self.gains) * a).astype(np.float32)
        return self.gains

    def lut(self) -> np.ndarray:
        """(256, 1, 3) uint8 table: per-channel gain, then a soft shoulder so bright values roll off instead of
        clipping (y = 1 - (1 - x)^p for the part above 0.7)."""
        key = tuple(np.round(self.gains, 3))
        if key == self._lut_key and self._lut is not None:
            return self._lut
        x = np.arange(256, dtype=np.float32) / 255.0
        cols = []
        for g in self.gains:
            v = x * g
            knee = 0.7
            over = v > knee
            room = 1.0 - knee
            v[over] = knee + room * (1.0 - np.exp(-(v[over] - knee) / room))   # smooth roll-off to 1.0
            cols.append(np.clip(v * 255.0 + 0.5, 0, 255).astype(np.uint8))
        self._lut = np.stack(cols, axis=1).reshape(256, 1, 3)
        self._lut_key = key
        return self._lut

    def apply(self, bgra: np.ndarray) -> np.ndarray:
        if np.allclose(self.gains, 1.0, atol=0.005):
            return bgra
        out = bgra.copy()
        out[..., :3] = cv2.LUT(np.ascontiguousarray(bgra[..., :3]), self.lut())
        return out
