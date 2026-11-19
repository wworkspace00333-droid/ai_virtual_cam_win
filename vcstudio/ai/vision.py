"""AI Vision (0.6.0 Phase 3): face landmarks + head pose, body pose and hand landmarks, on one background thread.

Settings section "vision": enabled, fps (default 6), face (YuNet landmarks + head pose), pose (MediaPipe pose), hands
(MediaPipe hands), overlay (draw them on the output).

  frame      the newest INPUT frame (before any effect), never queued; the output thread never waits for this
  face       vcstudio/ai/onnx_engine.YuNetFaceDetector (the same detector as "Hide face") -> 5 points per face,
             vcstudio/ai/head_pose.estimate -> yaw / pitch / roll
  pose       vcstudio/ai/pose.PoseEstimator (up to 2 people)
  hands      when gesture control already runs, its newest hands are reused (no second hand detector, no second
             inference); otherwise vcstudio/ai/hands.HandTracker runs here
  result     one VisionResult in a LatestSlot, stamped with the frame's pts; consumers drop results older than
             STALE_S (an overlay from a second ago would be in the wrong place)
  rate       fps × rate_scale; registered with the ResourceScheduler as the lowest-priority module ("vision"), so
             under load it slows first and the camera keeps its frame rate
  events     "hand_raised": a wrist above the nose (or well above the shoulders) held for HAND_HOLD_S, fired once
             until the hand comes down for HAND_RELEASE_S; listeners get the event name (automation rules use it
             as a gesture trigger)
  framing    framing_objects(result): people (visible pose landmarks, head room added) and faces for auto-framing
             when the detector is off or sees nobody
Measured on the 2-core development server without a GPU (CPU, 1080p input): face ≈ 10–15 ms, pose ≈ 30 ms per
person, hands ≈ 25 ms; together ≈ 60–80 ms per analysed frame, i.e. about 6 fps at most there.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

import numpy as np

from vcstudio.core.frame_queue import LatestSlot

log = logging.getLogger(__name__)

STALE_S = 0.5
HAND_HOLD_S = 0.6
HAND_RELEASE_S = 0.5


@dataclass(frozen=True)
class VisionConfig:
    enabled: bool = False
    fps: float = 6.0
    face: bool = True
    pose: bool = True
    hands: bool = False
    overlay: bool = True

    @staticmethod
    def from_settings(settings) -> VisionConfig:
        try:
            d = settings.get("vision") or {}
        except KeyError:
            d = {}
        try:
            fps = min(30.0, max(1.0, float(d.get("fps", 6))))
        except (TypeError, ValueError):
            fps = 6.0
        return VisionConfig(bool(d.get("enabled", False)), fps, bool(d.get("face", True)), bool(d.get("pose", True)),
                            bool(d.get("hands", False)), bool(d.get("overlay", True)))


@dataclass
class FaceInfo:
    box: tuple[float, float, float, float]
    points: np.ndarray
    pose: object = None                         # head_pose.HeadPose | None


@dataclass
class VisionResult:
    pts: float
    width: int
    height: int
    faces: list[FaceInfo] = field(default_factory=list)
    poses: list = field(default_factory=list)    # pose.Pose
    hands: list = field(default_factory=list)    # hands.Hand
    ms: dict = field(default_factory=dict)


@dataclass
class VisionStats:
    state: str = "stopped"                       # stopped | running | error | no_models
    fps: float = 0.0
    ms: float = 0.0
    parts_ms: dict = field(default_factory=dict)
    faces: int = 0
    poses: int = 0
    hands: int = 0
    error: str = ""
    missing: list = field(default_factory=list)  # parts switched on whose models are not downloaded


class VisionService:
    def __init__(self, capture, settings, gestures=None, log_event=None, cache_dir=None, factories=None):
        self.capture, self.settings, self.gestures = capture, settings, gestures
        self._log = log_event or (lambda lv, m: None)
        self.cache_dir = cache_dir
        self.factories = factories or {}         # tests: {"face": fn, "pose": fn, "hands": fn} -> analyser
        self.cfg = VisionConfig()
        self.slot: LatestSlot[VisionResult] = LatestSlot()
        self.stats = VisionStats()
        self.rate_scale = 1.0
        self.listeners: list = []                # callables(event name) for body events ("hand_raised")
        self._hand_since: float | None = None
        self._hand_down_since: float | None = None
        self._hand_armed = True
        self.events_fired = 0
        self._stop = threading.Event()
        self._t: threading.Thread | None = None

    # ------------------------------------------------------------------ control
    @property
    def running(self) -> bool:
        return self._t is not None and self._t.is_alive()

    def rate_fps(self) -> float:
        return self.stats.fps

    def apply_settings(self) -> None:
        cfg = VisionConfig.from_settings(self.settings)
        restart = cfg != self.cfg and self.running
        self.cfg = cfg
        if restart:
            self.stop()
        if cfg.enabled and not self.running:
            self.start()
        elif not cfg.enabled and self.running:
            self.stop()

    def start(self) -> None:
        if self.running:
            return
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, args=(self._stop,), name="vision", daemon=True)
        self._t.start()

    def stop(self) -> None:
        self._stop.set()
        if self._t is not None:
            self._t.join(3)
        self._t = None
        self.slot.clear()
        self.stats.state = "stopped"

    def result_for(self, pts: float) -> VisionResult | None:
        """The newest result if it belongs near `pts` (frame time), else None (stale results are not drawn)."""
        r, _ = self.slot.peek()
        if r is None or abs(pts - r.pts) > STALE_S:
            return None
        return r

    # ------------------------------------------------------------------ analysers
    def _make(self, part: str):
        if part in self.factories:
            return self.factories[part]()
        if part == "face":
            from vcstudio.ai.onnx_engine import YuNetFaceDetector, yunet_path
            return YuNetFaceDetector() if yunet_path().exists() else None
        if part == "pose":
            from vcstudio.ai import pose
            return pose.PoseEstimator(self.cache_dir) if pose.available() else None
        if part == "hands":
            from vcstudio.ai import hands
            return hands.HandTracker("all", self.cache_dir) if hands.available() else None
        return None

    def _run(self, stop: threading.Event) -> None:
        cfg = self.cfg
        parts: dict = {}
        missing = []
        try:
            for part, on in (("face", cfg.face), ("pose", cfg.pose), ("hands", cfg.hands)):
                if not on or (part == "hands" and self._gesture_hands_live()):
                    continue
                a = self._make(part)
                if a is None:
                    missing.append(part)
                else:
                    parts[part] = a
        except Exception as e:
            self.stats.state, self.stats.error = "error", f"{type(e).__name__}: {e}"
            self._log("ERROR", f"vision: models did not load: {self.stats.error}")
            return
        self.stats.missing = missing
        if not parts and not (cfg.hands and self._gesture_hands_live()):
            self.stats.state = "no_models"
            return
        self.stats.state, self.stats.error = "running", ""
        from vcstudio.core.frame import Frame
        last_ver = None
        n, t_win = 0, time.monotonic()
        while not stop.is_set():
            t0 = time.monotonic()
            frame, ver = self.capture.slot.peek()
            if frame is not None and ver != last_ver:
                last_ver = ver
                try:
                    res = self.analyse(frame, parts, cfg, Frame)
                    self.slot.put(res)
                    self.body_events(res, time.monotonic())
                    st = self.stats
                    st.faces, st.poses, st.hands = len(res.faces), len(res.poses), len(res.hands)
                    st.parts_ms = res.ms
                    st.ms = (time.monotonic() - t0) * 1000
                    n += 1
                except Exception as e:
                    self.stats.error = f"{type(e).__name__}: {e}"
                    log.exception("vision frame failed")
            if time.monotonic() - t_win >= 2.0:
                self.stats.fps, n, t_win = n / (time.monotonic() - t_win), 0, time.monotonic()
            target = max(0.5, cfg.fps * max(0.1, self.rate_scale))
            stop.wait(max(0.0, 1.0 / target - (time.monotonic() - t0)))

    def body_events(self, res: VisionResult, now: float) -> None:
        """Fire "hand_raised" once per raise (held HAND_HOLD_S; re-armed after HAND_RELEASE_S down)."""
        up = hand_raised(res.poses)
        if up:
            self._hand_down_since = None
            if self._hand_since is None:
                self._hand_since = now
            if self._hand_armed and now - self._hand_since >= HAND_HOLD_S:
                self._hand_armed = False
                self.events_fired += 1
                for cb in tuple(self.listeners):
                    try:
                        cb("hand_raised")
                    except Exception:
                        log.exception("vision listener failed")
        else:
            self._hand_since = None
            if self._hand_down_since is None:
                self._hand_down_since = now
            if now - self._hand_down_since >= HAND_RELEASE_S:
                self._hand_armed = True

    def _gesture_hands_live(self) -> bool:
        g = self.gestures
        return g is not None and getattr(g, "running", False) and getattr(g.stats, "state", "") == "running"

    def analyse(self, frame, parts: dict, cfg: VisionConfig, Frame=None) -> VisionResult:
        from vcstudio.ai import head_pose
        res = VisionResult(frame.pts, frame.width, frame.height)
        with frame.bgra() as a:
            bgr = np.ascontiguousarray(a[..., :3])
        if "face" in parts:
            t = time.perf_counter()
            faces, _ = parts["face"].detect(frame)
            for f in faces:
                if f.points is None:
                    continue
                res.faces.append(FaceInfo((f.x0, f.y0, f.x1, f.y1), f.points,
                                          head_pose.estimate(f.points, frame.width, frame.height)))
            res.ms["face"] = round((time.perf_counter() - t) * 1000, 1)
        if "pose" in parts:
            t = time.perf_counter()
            res.poses, _ = parts["pose"].estimate(bgr)
            res.ms["pose"] = round((time.perf_counter() - t) * 1000, 1)
        if cfg.hands:
            if "hands" in parts:
                t = time.perf_counter()
                res.hands = parts["hands"].hands(bgr)
                res.ms["hands"] = round((time.perf_counter() - t) * 1000, 1)
            elif self._gesture_hands_live() and abs(self.gestures.last_hands_pts - frame.pts) <= STALE_S:
                res.hands = list(self.gestures.last_hands)          # reused, not detected twice
        return res


def hand_raised(poses, thr: float = 0.5) -> bool:
    """A wrist (15 / 16) above the nose, or above the shoulder line by half the shoulder width (pure: tested)."""
    for p in poses:
        L, vis = p.landmarks, p.visibility
        sh = [i for i in (11, 12) if vis[i] >= thr]
        for w in (15, 16):
            if vis[w] < thr:
                continue
            if vis[0] >= thr and L[w, 1] < L[0, 1]:
                return True
            if len(sh) == 2:
                width = abs(L[11, 0] - L[12, 0])
                if L[w, 1] < min(L[11, 1], L[12, 1]) - 0.5 * width:
                    return True
    return False


@dataclass(frozen=True)
class FramingObject:
    label: str
    x0: float
    y0: float
    x1: float
    y1: float


def framing_objects(r: VisionResult, thr: float = 0.5) -> list[FramingObject]:
    """People from pose landmarks (with head room) and faces, in the shape auto-framing reads."""
    out = []
    for p in r.poses:
        pts = p.landmarks[p.visibility >= thr, :2]
        if len(pts) < 3:
            continue
        x0, y0 = pts.min(0)
        x1, y1 = pts.max(0)
        head = 0.15 * (y1 - y0)                    # landmarks stop at the eyes / nose: leave room above the head
        out.append(FramingObject("person", float(x0), float(max(0.0, y0 - head)), float(x1), float(y1)))
    for f in r.faces:
        out.append(FramingObject("face", *(float(v) for v in f.box)))
    return out


HAND_EDGES = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11), (11, 12),
              (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (0, 17), (17, 18), (18, 19), (19, 20)]


def draw(bgra: np.ndarray, r: VisionResult) -> None:
    """Overlays in place: face points + facing direction, body skeletons, hand skeletons."""
    import cv2

    from vcstudio.ai import head_pose, pose
    for f in r.faces:
        head_pose.draw(bgra, f.points, f.pose)
    pose.draw(bgra, r.poses)
    for h in r.hands:
        L = np.asarray(h.landmarks)
        for a, b in HAND_EDGES:
            cv2.line(bgra, (int(L[a, 0]), int(L[a, 1])), (int(L[b, 0]), int(L[b, 1])), (80, 255, 80, 255), 2,
                     cv2.LINE_AA)
        for x, y in L[:, :2]:
            cv2.circle(bgra, (int(x), int(y)), 3, (255, 255, 255, 255), -1, cv2.LINE_AA)
