"""Head pose from YuNet's five face landmarks (0.6.0 Phase 3): yaw, pitch and roll with OpenCV solvePnP.

Five points (both eyes, nose tip, both mouth corners) are matched to a generic 3-D face (millimetres, average adult
proportions); the camera is assumed to have a ~60° horizontal field of view (focal length = frame width / (2·tan 30°))
and no distortion. Accuracy is a few degrees for faces turned less than ~45° and gets worse beyond (a profile face
hides an eye: YuNet then guesses the hidden points). Good enough for framing ("look room" in the direction the
person faces) and for "is the person facing the camera"; it is not gaze tracking and identifies nobody.

Angles (degrees): yaw + = the person turns to the picture's right, pitch + = looks up, roll + = head tilts so the
picture's right eye is lower.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

# generic face, x right (picture), y down, z towards the camera; nose tip at the origin
FACE_3D = np.array([[-31.0, -32.0, -26.0],     # eye on the picture's left (YuNet point 0: the person's right eye)
                    [31.0, -32.0, -26.0],      # eye on the picture's right
                    [0.0, 0.0, 0.0],           # nose tip
                    [-24.0, 30.0, -22.0],      # mouth corner, picture's left
                    [24.0, 30.0, -22.0]],      # mouth corner, picture's right
                   np.float64)
HFOV_DEG = 60.0


@dataclass(frozen=True)
class HeadPose:
    yaw: float
    pitch: float
    roll: float
    rvec: tuple
    tvec: tuple

    @property
    def facing(self) -> bool:
        return abs(self.yaw) < 25 and abs(self.pitch) < 25


def camera_matrix(w: int, h: int, hfov_deg: float = HFOV_DEG) -> np.ndarray:
    f = w / (2 * math.tan(math.radians(hfov_deg) / 2))
    return np.array([[f, 0, w / 2], [0, f, h / 2], [0, 0, 1]], np.float64)


def estimate(points: np.ndarray, w: int, h: int) -> HeadPose | None:
    """HeadPose from (5, 2) landmarks in pixels of a w x h picture; None when the fit fails."""
    pts = np.asarray(points, np.float64).reshape(5, 2)
    if not np.all(np.isfinite(pts)):
        return None
    K = camera_matrix(w, h)
    ok, rvec, tvec = cv2.solvePnP(FACE_3D, pts, K, None, flags=cv2.SOLVEPNP_EPNP)
    if not ok:
        return None
    ok, rvec, tvec = cv2.solvePnP(FACE_3D, pts, K, None, rvec, tvec, useExtrinsicGuess=True,
                                  flags=cv2.SOLVEPNP_ITERATIVE)
    if not ok or tvec[2, 0] <= 0:
        return None
    R, _ = cv2.Rodrigues(rvec)
    yaw, pitch, roll = angles(R)
    return HeadPose(round(yaw, 1), round(pitch, 1), round(roll, 1), tuple(rvec.ravel()), tuple(tvec.ravel()))


def angles(R: np.ndarray) -> tuple[float, float, float]:
    """(yaw, pitch, roll) in degrees from a rotation matrix (model -> camera), in this module's conventions."""
    sy = math.hypot(R[0, 0], R[1, 0])
    if sy > 1e-6:
        pitch = math.degrees(math.atan2(R[2, 1], R[2, 2]))
        yaw = math.degrees(math.atan2(-R[2, 0], sy))
        roll = math.degrees(math.atan2(R[1, 0], R[0, 0]))
    else:
        pitch = math.degrees(math.atan2(-R[1, 2], R[1, 1]))
        yaw = math.degrees(math.atan2(-R[2, 0], sy))
        roll = 0.0
    return yaw, pitch, roll


def rotation(yaw: float, pitch: float, roll: float) -> np.ndarray:
    """Inverse of angles(): the rotation matrix for given angles (for tests and overlays)."""
    y, p, r = math.radians(yaw), math.radians(pitch), math.radians(roll)
    Rx = np.array([[1, 0, 0], [0, math.cos(p), -math.sin(p)], [0, math.sin(p), math.cos(p)]])
    Ry = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]])
    Rz = np.array([[math.cos(r), -math.sin(r), 0], [math.sin(r), math.cos(r), 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def draw(bgra: np.ndarray, points: np.ndarray, pose: HeadPose | None) -> None:
    """Landmarks (yellow) and the head's facing direction (a line from the nose) in place."""
    for x, y in np.asarray(points).reshape(-1, 2):
        cv2.circle(bgra, (int(x), int(y)), 3, (0, 230, 255, 255), -1, cv2.LINE_AA)
    if pose is None:
        return
    h, w = bgra.shape[:2]
    K = camera_matrix(w, h)
    tip, _ = cv2.projectPoints(np.array([[0.0, 0.0, 60.0]]), np.array(pose.rvec), np.array(pose.tvec), K, None)
    nose = np.asarray(points).reshape(5, 2)[2]
    cv2.line(bgra, (int(nose[0]), int(nose[1])), (int(tip[0, 0, 0]), int(tip[0, 0, 1])), (0, 140, 255, 255), 2,
             cv2.LINE_AA)
