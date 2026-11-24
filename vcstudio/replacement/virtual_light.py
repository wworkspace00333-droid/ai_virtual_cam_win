"""Virtual studio lighting (0.7.0): key, fill and rim light painted onto the person (profile section "lighting").

A 2-D relighting model, not a 3-D one: no normals are estimated, so it cannot cast real shadows. Per frame:
  where      the largest face (AI detection "face" boxes or AI Vision faces) places the lights; without a face the
             frame is left as it is (nothing to aim at)
  who        the person mask of the background segmentation when it runs (the background stays unlit), else a soft
             ellipse around the face and shoulders
  key        a broad soft spot beside the face on the key side (`key_angle` -90 = from the picture's left, +90 =
             from the right, 0 = from the front), plus a gradient across the face: the lit side brighter, the far
             side kept darker unless `fill` lifts it — the key-to-fill ratio photographers use
  rim        a thin band along the person's outline on the side away from the key, as a backlight would
  colour     each light has a colour temperature (2700 K warm .. 7500 K cool), relative to daylight (6500 K)
  softness   spot size and how gradual the gradient is
The light maps are computed at 1/4 size (at most MAP_SIDE wide) and applied in 16-bit fixed point to the rectangle
the lights reach. Measured in tests/test_virtual_light.py on the development server (2 cores): about 28 ms per 1080p
frame with a face a third of the picture high (too slow for 60 fps there); the cost grows with the lit area.
Applied right after automatic exposure, before colour grading, background and regions: like a real light on the
person, under the user's own pictures.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

PRESETS: dict[str, dict] = {
    "studio": {"vl_key": 0.45, "vl_key_angle": -40.0, "vl_key_temp": 5600.0, "vl_fill": 0.25, "vl_rim": 0.35,
               "vl_rim_temp": 6500.0, "vl_softness": 0.6},
    "soft": {"vl_key": 0.3, "vl_key_angle": -15.0, "vl_key_temp": 5200.0, "vl_fill": 0.45, "vl_rim": 0.15,
             "vl_rim_temp": 6000.0, "vl_softness": 0.9},
    "dramatic": {"vl_key": 0.7, "vl_key_angle": -75.0, "vl_key_temp": 4800.0, "vl_fill": 0.0, "vl_rim": 0.6,
                 "vl_rim_temp": 7500.0, "vl_softness": 0.35},
    "warm": {"vl_key": 0.4, "vl_key_angle": -30.0, "vl_key_temp": 3200.0, "vl_fill": 0.3, "vl_rim": 0.25,
             "vl_rim_temp": 3800.0, "vl_softness": 0.7},
    "cool": {"vl_key": 0.4, "vl_key_angle": 30.0, "vl_key_temp": 7000.0, "vl_fill": 0.3, "vl_rim": 0.3,
             "vl_rim_temp": 7500.0, "vl_softness": 0.7},
}
WORK = 4                     # light maps at 1/WORK size ...
MAP_SIDE = 320               # ... and at most this long side (the light is smooth: a coarse map is enough)


def kelvin_bgr(k: float) -> np.ndarray:
    """Colour of a black body at k kelvin as BGR gains relative to 6500 K (Tanner Helland's fit) (pure: tested)."""
    def rgb(t: float) -> tuple[float, float, float]:
        t = max(1000.0, min(40000.0, t)) / 100.0
        r = 255.0 if t <= 66 else 329.698727446 * (t - 60) ** -0.1332047592
        g = (99.4708025861 * math.log(t) - 161.1195681661) if t <= 66 else 288.1221695283 * (t - 60) ** -0.0755148492
        b = 255.0 if t >= 66 else (0.0 if t <= 19 else 138.5177312231 * math.log(t - 10) - 305.0447927307)
        return tuple(min(255.0, max(0.0, v)) for v in (r, g, b))  # type: ignore[return-value]
    r, g, b = rgb(k)
    r0, g0, b0 = rgb(6500.0)
    return np.array([b / max(1.0, b0), g / max(1.0, g0), r / max(1.0, r0)], np.float32)


@dataclass(frozen=True)
class LightRig:
    key: float = 0.45
    key_angle: float = -40.0
    key_temp: float = 5600.0
    fill: float = 0.25
    rim: float = 0.35
    rim_temp: float = 6500.0
    softness: float = 0.6

    @property
    def active(self) -> bool:
        return self.key > 0 or self.fill > 0 or self.rim > 0


def person_ellipse(w: int, h: int, face) -> np.ndarray:
    """Soft (h, w) float32 head-and-shoulders mask from a face box (when no segmentation mask is available)."""
    x0, y0, x1, y1 = face
    fw, fh = x1 - x0, y1 - y0
    m = np.zeros((h, w), np.float32)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    cv2.ellipse(m, (int(cx), int(cy)), (int(fw * 0.62), int(fh * 0.72)), 0, 0, 360, 1.0, -1)
    cv2.ellipse(m, (int(cx), int(cy + fh * 1.6)), (int(fw * 1.6), int(fh * 1.1)), 0, 0, 360, 1.0, -1)
    k = max(3, int(fw * 0.3)) | 1
    return cv2.GaussianBlur(m, (k, k), 0)


def light_maps(w: int, h: int, face, person: np.ndarray, rig: LightRig) -> tuple[np.ndarray, np.ndarray]:
    """(key+fill gain map, rim map), (h, w) float32 each, for a person mask (h, w) 0..1 (pure: tested)."""
    x0, y0, x1, y1 = face
    fw, fh = max(1.0, x1 - x0), max(1.0, y1 - y0)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    side = math.sin(math.radians(max(-90.0, min(90.0, rig.key_angle))))     # -1 left .. +1 right
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    soft = 0.4 + 0.8 * max(0.0, min(1.0, rig.softness))
    # key spot beside the face on the key side
    sx, sy = cx + side * fw * 0.55, cy - fh * 0.15
    r = fh * 1.3 * soft
    spot = np.exp(-((xx - sx) ** 2 + (yy - sy) ** 2) / (2 * r * r))
    # across the face: +1 on the key side, -1 on the far side (soft tanh over a face width)
    across = np.tanh((xx - cx) * side / (fw * 0.5 * soft)) if abs(side) > 0.05 else np.zeros_like(xx)
    shade = np.clip(-across, 0.0, 1.0)                                      # the side away from the key
    key = rig.key * (0.6 * spot + 0.4 * np.clip(across, 0, 1) * spot) - rig.key * 0.35 * shade * spot
    fill = rig.fill * 0.35 * (shade * spot + 0.3 * spot)
    gain = (key + fill) * person
    # rim: the outline band of the person, on the side away from the key (all round for a front key)
    k = max(3, int(fw * 0.12)) | 1
    inner = cv2.GaussianBlur(person, (k * 3 | 1, k * 3 | 1), 0)
    band = np.clip(person - inner, 0.0, 1.0) * 4.0
    away = np.clip(0.5 - 0.5 * np.tanh((xx - cx) * side / (fw * 0.8)), 0, 1) if abs(side) > 0.05 else 0.6
    rim = rig.rim * np.clip(band, 0, 1) * away * person
    return gain.astype(np.float32), np.asarray(rim, np.float32)


def relight(bgra: np.ndarray, face, rig: LightRig, person_small: np.ndarray | None = None) -> np.ndarray:
    """A relit copy of `bgra` (or `bgra` itself when the rig does nothing). `person_small` is any-size 0..1 mask."""
    if not rig.active or face is None:
        return bgra
    H, W = bgra.shape[:2]
    k = max(WORK, -(-max(W, H) // MAP_SIDE))
    w, h = max(8, W // k), max(8, H // k)
    s = w / W
    f = tuple(v * s for v in face)
    if person_small is not None:
        person = cv2.resize(np.asarray(person_small, np.float32), (w, h), interpolation=cv2.INTER_LINEAR)
    else:
        person = person_ellipse(w, h, f)
    gain, rim = light_maps(w, h, f, np.clip(person, 0, 1), rig)
    # only the rectangle where the lights change anything is processed at full size
    ys, xs = np.nonzero((np.abs(gain) > 0.004) | (rim > 0.004))
    if not len(xs):
        return bgra
    bx0, by0 = max(0, xs.min() - 1), max(0, ys.min() - 1)
    bx1, by1 = min(w, xs.max() + 2), min(h, ys.max() + 2)
    X0, Y0, X1, Y1 = int(bx0 * W / w), int(by0 * H / h), min(W, int(np.ceil(bx1 * W / w))), min(H, int(np.ceil(by1 * H / h)))
    kt, rt = kelvin_bgr(rig.key_temp), kelvin_bgr(rig.rim_temp)
    # fixed point (x 256) in 16 bits: integer SIMD instead of float images (about half the time at 1080p)
    g3 = np.clip((1.0 + gain[by0:by1, bx0:bx1, None] * kt[None, None]) * 256.0, 0, 65535).astype(np.uint16)
    add = np.clip(rim[by0:by1, bx0:bx1, None] * rt[None, None] * 255.0, 0, 255).astype(np.uint16)
    size = (X1 - X0, Y1 - Y0)
    g3 = cv2.resize(g3, size, interpolation=cv2.INTER_LINEAR)
    add = cv2.resize(add, size, interpolation=cv2.INTER_LINEAR)
    out = bgra.copy()
    roi = np.ascontiguousarray(out[Y0:Y1, X0:X1, :3])
    prod = cv2.multiply(roi, g3, scale=1.0 / 256.0, dtype=cv2.CV_16U)
    cv2.add(prod, add, dst=prod)
    out[Y0:Y1, X0:X1, :3] = cv2.convertScaleAbs(prod)                      # saturating cast to 0..255
    return out
