"""Eye contact correction (0.7.0): the irises are moved towards where they would be when looking into the camera.

From the face mesh (478 points with irises) and the gaze estimate (vcstudio/ai/gaze.py): per open eye the iris
position for "looking at the camera" is computed from the gaze offset (the eye turns by the offset angle, the iris
moves sin(angle) * eyeball radius), and the picture inside the eye opening is warped (cv2.remap on a small patch
around the eye) with a smooth bump centred on the iris. The lids and everything outside the opening stay where they
are, so the eye shape does not change; the warp is feathered at the opening's edge.

Safe fallback (no change at all, never a half-warped eye): the gaze confidence is under MIN_CONFIDENCE (blink, head
turned, weak mesh), the needed correction is larger than MAX_CORRECT_DEG (a person looking clearly elsewhere is not
made to stare), or the mesh is older than MAX_AGE_S (the head moved since: a stale warp would smear the face).
Strength 0..1 scales the shift; the shift is capped at MAX_SHIFT of half the eye width.

Cost: two patches of about the eye's size per face, on the CPU (no GPU path yet). Measured in tests/test_gaze.py on
the development server (2 cores): 8 ms per face with 160 px wide eyes, a face filling most of a 720p picture; the
patch, and so the cost, grows with the eye's area. Geometry only: nothing identifies a person.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

from vcstudio.ai.gaze import EYE_GAIN, EYES, Gaze

MIN_CONFIDENCE = 0.6
MAX_CORRECT_DEG = 25.0
MAX_SHIFT = 0.35
MAX_AGE_S = 0.15
RADIUS_GAIN = 1.6          # bump radius / iris radius


def target_shift(gaze: Gaze, half: float, strength: float) -> tuple[float, float]:
    """(dx, dy) pixels to move an iris so the eye looks into the camera (pure: tested).
    off_yaw > 0 (looks to the picture's right of the camera): the iris moves left; off_pitch > 0 (above): down."""
    r = half / EYE_GAIN                                 # eyeball radius in pixels
    dx = -math.sin(math.radians(max(-60.0, min(60.0, gaze.off_yaw)))) * r
    dy = math.sin(math.radians(max(-60.0, min(60.0, gaze.off_pitch)))) * r
    s = max(0.0, min(1.0, strength))
    dx, dy = dx * s, dy * s
    cap = MAX_SHIFT * half
    n = math.hypot(dx, dy)
    if n > cap:
        dx, dy = dx * cap / n, dy * cap / n
    return dx, dy


def warp_eye(img: np.ndarray, contour: np.ndarray, centre, radius: float, shift) -> bool:
    """Move the iris at `centre` by `shift` inside the eye opening `contour` ((K, 2) pixels), in place.
    Returns False (nothing changed) when the patch is outside the picture or degenerate."""
    H, W = img.shape[:2]
    dx, dy = shift
    if abs(dx) < 0.2 and abs(dy) < 0.2:
        return False
    c = np.asarray(contour, np.float32)
    pad = int(math.ceil(max(radius * 2.0, abs(dx), abs(dy)) + 3))
    x0, y0 = (np.floor(c.min(0)) - pad).astype(int)
    x1, y1 = (np.ceil(c.max(0)) + pad).astype(int)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
    if x1 - x0 < 6 or y1 - y0 < 4:
        return False
    patch = img[y0:y1, x0:x1]
    ph, pw = patch.shape[:2]
    mask = np.zeros((ph, pw), np.uint8)
    cv2.fillPoly(mask, [np.round(c - (x0, y0)).astype(np.int32)], 255)
    if not mask.any():
        return False
    feather = max(1, int(radius * 0.35)) * 2 + 1
    m = cv2.GaussianBlur(mask, (feather, feather), 0).astype(np.float32) / 255.0
    m *= mask > 0                                       # never pull pixels outside the opening
    yy, xx = np.mgrid[0:ph, 0:pw].astype(np.float32)
    cx, cy = centre[0] - x0 + dx, centre[1] - y0 + dy  # bump around the iris's new place
    sig = max(1.0, radius * RADIUS_GAIN)
    bump = np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * sig * sig)) * m
    map_x = (xx - dx * bump).astype(np.float32)
    map_y = (yy - dy * bump).astype(np.float32)
    warped = cv2.remap(patch, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    a = m[..., None]
    patch[:] = (warped.astype(np.float32) * a + patch.astype(np.float32) * (1 - a) + 0.5).astype(np.uint8)
    return True


def correct(img: np.ndarray, points: np.ndarray, gaze: Gaze | None, strength: float) -> int:
    """Correct both eyes of one face in place; the number of eyes changed (0: fallback, picture untouched)."""
    if gaze is None or gaze.confidence < MIN_CONFIDENCE or strength <= 0:
        return 0
    if math.hypot(gaze.off_yaw, gaze.off_pitch) > MAX_CORRECT_DEG:
        return 0
    p = np.asarray(points, np.float32)[:, :2]
    n = 0
    for m in gaze.eyes:
        eye = min(EYES, key=lambda k: (p[EYES[k]["iris"]][0] - m.centre[0]) ** 2 +
                  (p[EYES[k]["iris"]][1] - m.centre[1]) ** 2)
        shift = target_shift(gaze, m.half, strength)
        if warp_eye(img, p[list(EYES[eye]["contour"])], m.centre, m.radius, shift):
            n += 1
    return n
