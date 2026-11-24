"""Eye gaze from the dense face mesh (0.7.0): where a person looks, compensated for head pose, with confidence.

Per eye the iris centre (mesh 468 / 473) is measured against the eye corners (33-133 / 362-263): `h` is its offset
along the corner line, `v` across it, both over half the eye width (-1 .. 1). The eyeball is about 24 mm across and
an eye opening about 30 mm wide, so the iris centre moves sin(angle) * 12 mm when the eye turns:
eye angle = asin(offset * EYE_GAIN), EYE_GAIN = 15 / 12. The head pose (head_pose.py, from YuNet's points) is added
(gaze in the camera's frame = head + eye-in-head), and the direction from the face to the camera is subtracted,
so "looking at the camera" also holds for a face at the edge of the picture.

Conventions as head_pose: yaw + = towards the picture's right, pitch + = up. Confidence 0..1 drops with a closing
eye (blink: openness under OPEN_MIN), a head turned beyond ~25-45° (one eye foreshortened) and a weak mesh score.
`calibration` ({"yaw", "pitch"} in degrees) is what the user's "I am looking at the camera" click measured; it is
subtracted, which absorbs a camera above / below the screen as well as the generic eye model's error.

Accuracy: a geometric estimate, a few degrees at best; it says "looks at the camera / to the left / down", not the
point on the screen. Geometry only: nothing identifies a person.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

import numpy as np

EYES = {  # picture's left eye (the person's right), picture's right eye: corners left -> right, lids, iris
    "left": {"corners": (33, 133), "upper": 159, "lower": 145, "iris": 468, "ring": (469, 470, 471, 472),
             "contour": (33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246)},
    "right": {"corners": (362, 263), "upper": 386, "lower": 374, "iris": 473, "ring": (474, 475, 476, 477),
              "contour": (263, 249, 390, 373, 374, 380, 381, 382, 362, 398, 384, 385, 386, 387, 388, 466)},
}
EYE_GAIN = 15.0 / 12.0
OPEN_MIN = 0.12            # lid gap / eye width under which the eye counts as closed
OPEN_FULL = 0.22           # ... and above which it is fully open (confidence 1)
AT_CAMERA_DEG = 10.0       # gaze within this of the camera direction: "looking at the camera"
SMOOTH_S = 0.3             # smoothing window
STABILITY_S = 1.0          # stability window
HFOV_DEG = 60.0            # same camera assumption as head_pose


@dataclass(frozen=True)
class EyeMeasure:
    h: float               # iris offset along the corner line (+ towards the picture's right), / half eye width
    v: float               # across it (+ down)
    openness: float        # lid gap / eye width
    centre: tuple          # iris centre (x, y) frame pixels
    radius: float          # iris radius, pixels
    half: float            # half eye width, pixels


@dataclass(frozen=True)
class Gaze:
    yaw: float             # gaze direction, camera frame (degrees)
    pitch: float
    off_yaw: float         # gaze minus the direction to the camera (0, 0: looking at the camera)
    off_pitch: float
    confidence: float      # 0..1
    stability: float = 1.0  # 0..1, 1 = steady over STABILITY_S
    eyes: tuple = ()       # EyeMeasure per open eye

    @property
    def at_camera(self) -> bool:
        return self.confidence >= 0.5 and math.hypot(self.off_yaw, self.off_pitch) <= AT_CAMERA_DEG

    @property
    def direction(self) -> str:
        """at_camera | left | right | up | down (the picture's sides) | unknown."""
        if self.confidence < 0.5:
            return "unknown"
        if self.at_camera:
            return "at_camera"
        if abs(self.off_yaw) >= abs(self.off_pitch):
            return "right" if self.off_yaw > 0 else "left"
        return "up" if self.off_pitch > 0 else "down"


def measure_eye(points: np.ndarray, eye: str) -> EyeMeasure | None:
    """Iris offset and openness of one eye from (478, 2+) mesh points (pure: tested)."""
    e = EYES[eye]
    p = np.asarray(points, np.float64)[:, :2]
    c0, c1 = p[e["corners"][0]], p[e["corners"][1]]
    axis = c1 - c0
    width = float(np.linalg.norm(axis))
    if width < 4 or len(p) < 478:
        return None
    u = axis / width
    n = np.array([-u[1], u[0]])                        # perpendicular, + down in the picture for a level eye
    half = width / 2
    mid = (c0 + c1) / 2
    iris = p[e["iris"]]
    d = iris - mid
    radius = float(np.mean([np.linalg.norm(p[i] - iris) for i in e["ring"]]))
    openness = float(np.linalg.norm(p[e["upper"]] - p[e["lower"]])) / width
    return EyeMeasure(float(d @ u) / half, float(d @ n) / half, openness, (float(iris[0]), float(iris[1])),
                      radius, half)


def eye_angles(h: float, v: float) -> tuple[float, float]:
    """(yaw, pitch) of the eye in the head from the iris offsets (pure)."""
    yaw = math.degrees(math.asin(max(-1.0, min(1.0, h * EYE_GAIN))))
    pitch = -math.degrees(math.asin(max(-1.0, min(1.0, v * EYE_GAIN))))
    return yaw, pitch


def camera_direction(x: float, y: float, w: int, h: int, hfov_deg: float = HFOV_DEG) -> tuple[float, float]:
    """(yaw, pitch) a face at pixel (x, y) must look in to look into the camera (pure)."""
    f = w / (2 * math.tan(math.radians(hfov_deg) / 2))
    return -math.degrees(math.atan((x - w / 2) / f)), math.degrees(math.atan((y - h / 2) / f))


def estimate(points: np.ndarray, head, w: int, h: int, score: float = 1.0,
             calibration: dict | None = None) -> Gaze | None:
    """Gaze of one face from its mesh points and head pose (head_pose.HeadPose or None: eyes only)."""
    eyes = [m for m in (measure_eye(points, "left"), measure_eye(points, "right")) if m is not None]
    if not eyes:
        return None
    open_ = [m for m in eyes if m.openness >= OPEN_MIN]
    use = open_ or eyes
    hh = float(np.mean([m.h for m in use]))
    vv = float(np.mean([m.v for m in use]))
    eyaw, epitch = eye_angles(hh, vv)
    hyaw = float(getattr(head, "yaw", 0.0) or 0.0)
    hpitch = float(getattr(head, "pitch", 0.0) or 0.0)
    yaw, pitch = hyaw + eyaw, hpitch + epitch
    cx = float(np.mean([m.centre[0] for m in eyes]))
    cy = float(np.mean([m.centre[1] for m in eyes]))
    tyaw, tpitch = camera_direction(cx, cy, w, h)
    cal = calibration or {}
    off_yaw = yaw - tyaw - float(cal.get("yaw", 0.0))
    off_pitch = pitch - tpitch - float(cal.get("pitch", 0.0))
    eye_conf = float(np.mean([min(1.0, max(0.0, (m.openness - OPEN_MIN) / (OPEN_FULL - OPEN_MIN))) for m in eyes]))
    head_conf = min(1.0, max(0.0, (45.0 - abs(hyaw)) / 20.0)) if head is not None else 0.7
    conf = round(eye_conf * head_conf * min(1.0, max(0.0, score)), 3)
    return Gaze(round(yaw, 1), round(pitch, 1), round(off_yaw, 1), round(off_pitch, 1), conf, 1.0, tuple(open_))


class GazeTracker:
    """Smoothing and stability per face over time. Faces are matched to the previous ones by position."""

    def __init__(self, max_faces: int = 4):
        self.max_faces = max_faces
        self.tracks: list[dict] = []                   # {"pos": (x, y), "hist": deque[(t, yaw, pitch, oy, op)]}

    def update(self, t: float, items: list[tuple[tuple[float, float], Gaze]]) -> list[Gaze]:
        out: list[Gaze] = []
        used: set[int] = set()
        new_tracks = []
        for pos, g in items[: self.max_faces]:
            best, bd = None, 1e18
            for i, tr in enumerate(self.tracks):
                if i in used:
                    continue
                d = (tr["pos"][0] - pos[0]) ** 2 + (tr["pos"][1] - pos[1]) ** 2
                if d < bd:
                    best, bd = i, d
            tr = self.tracks[best] if best is not None and bd < 200.0 ** 2 else {"hist": deque()}
            if best is not None and bd < 200.0 ** 2:
                used.add(best)
            tr["pos"] = pos
            hist: deque = tr["hist"]
            if g.confidence >= 0.3:                    # a blink does not drag the smoothed gaze
                hist.append((t, g.yaw, g.pitch, g.off_yaw, g.off_pitch))
            while hist and t - hist[0][0] > STABILITY_S:
                hist.popleft()
            recent = [x for x in hist if t - x[0] <= SMOOTH_S] or list(hist)[-1:]
            if recent:
                a = np.array(recent)[:, 1:]
                m = a.mean(0)
                spread = float(np.array(hist)[:, 1:3].std(0).max()) if len(hist) >= 3 else 0.0
                g = Gaze(round(float(m[0]), 1), round(float(m[1]), 1), round(float(m[2]), 1), round(float(m[3]), 1),
                         g.confidence, round(max(0.0, 1.0 - spread / 10.0), 2), g.eyes)
            out.append(g)
            new_tracks.append(tr)
        self.tracks = new_tracks
        return out

    def reset(self) -> None:
        self.tracks = []


def draw(bgra: np.ndarray, gaze: Gaze) -> None:
    """An arrow from each open eye along the gaze (green when at the camera) in place."""
    import cv2
    col = (80, 220, 80, 255) if gaze.at_camera else (0, 170, 255, 255)
    for m in gaze.eyes:
        L = m.half * 2.2
        dx = math.sin(math.radians(gaze.off_yaw)) * L
        dy = -math.sin(math.radians(gaze.off_pitch)) * L
        x, y = m.centre
        cv2.circle(bgra, (int(x), int(y)), max(2, int(m.radius)), col, 1, cv2.LINE_AA)
        cv2.arrowedLine(bgra, (int(x), int(y)), (int(x + dx), int(y + dy)), col, 2, cv2.LINE_AA, tipLength=0.25)
