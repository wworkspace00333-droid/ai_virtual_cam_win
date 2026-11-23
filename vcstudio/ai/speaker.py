"""Active-speaker camera switching by mouth movement (0.6.0 Phase 2, multi-camera auto mode "speaker").

One microphone cannot tell which camera the voice belongs to, so the signal is visual: on each source (the main
camera and the enabled extra sources) the largest face is found (YuNet), its face mesh is computed, and the mouth
opening is measured as the distance of the inner lips (mesh points 13 / 14) over the face height (10 / 152). Speech
opens and closes the mouth several times a second; a still face, a smile or a held-open mouth hardly changes it. The
activity of a source is the standard deviation of that opening over the last WINDOW_S seconds.

The multi-camera switcher (vcstudio/core/multicam.AutoSwitcher) takes the source this service names and applies its
own hold time and cooldown, so short gestures (a laugh, chewing) do not switch the picture. Nothing identifies a
person: only the lips' movement is measured, and nothing is stored.

Cost: per analysed source one face detection on a copy of at most DETECT_SIDE pixels plus one face mesh. Measured
on the development server (2 cores, CPU): 78 ms per pass over three 720p sources, so about 47 % of one core at the
default 6 passes per second. THRESHOLD was set on synthetic mouth movement (a close-up from bus.jpg with a drawn
mouth: talking 0.015, still 0.000); real speech is part of the hardware checklist.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

import cv2
import numpy as np

log = logging.getLogger(__name__)

WINDOW_S = 1.5               # activity window
MIN_SAMPLES = 5              # fewer measurements in the window: activity 0 (not enough evidence)
THRESHOLD = 0.012            # activity (std of the opening / face height) that counts as speaking
MARGIN = 1.5                 # the leader must be this many times as active as the next source
DETECT_SIDE = 480            # long side of the copy the face detector runs on


def mouth_open(points: np.ndarray) -> float:
    """Inner-lip distance over the face height for a (478, 2+) face mesh (pure: tested)."""
    p = np.asarray(points, np.float32)
    face = float(np.linalg.norm(p[10, :2] - p[152, :2]))
    return float(np.linalg.norm(p[13, :2] - p[14, :2])) / face if face > 1 else 0.0


class MouthTrack:
    """Openings of one source over time and their activity."""

    def __init__(self):
        self.samples: deque[tuple[float, float]] = deque()

    def add(self, t: float, opening: float | None) -> None:
        """`None`: no face this time; the old samples age out on their own."""
        if opening is not None:
            self.samples.append((t, opening))
        while self.samples and t - self.samples[0][0] > WINDOW_S:
            self.samples.popleft()

    def activity(self, now: float) -> float:
        v = [o for t, o in self.samples if now - t <= WINDOW_S]
        return float(np.std(v)) if len(v) >= MIN_SAMPLES else 0.0


def pick_speaker(activity: dict[str, float], threshold: float = THRESHOLD, margin: float = MARGIN) -> str | None:
    """The source whose mouth moves clearly the most, or None when nobody / several equally (pure: tested)."""
    if not activity:
        return None
    ranked = sorted(activity.items(), key=lambda kv: -kv[1])
    best, a = ranked[0]
    if a < threshold:
        return None
    if len(ranked) > 1 and a < margin * ranked[1][1]:
        return None
    return best


@dataclass
class SpeakerStats:
    state: str = "stopped"           # stopped | running | no_models | error
    runs: int = 0
    ms: float = 0.0
    activity: dict = field(default_factory=dict)
    speaker: str | None = None
    error: str = ""


class SpeakerService:
    """Thread measuring mouth movement on every source. `frames()` returns {source id: Frame | None}."""

    def __init__(self, frames: Callable[[], dict], fps: float = 6.0, detector_factory=None, mesh_factory=None):
        self.frames = frames
        self.fps = fps
        self.detector_factory = detector_factory
        self.mesh_factory = mesh_factory
        self.stats = SpeakerStats()
        self.tracks: dict[str, MouthTrack] = {}
        self._versions: dict[str, int | None] = {}
        self._stop = threading.Event()
        self._t: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._t is not None and self._t.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, args=(self._stop,), name="speaker", daemon=True)
        self._t.start()

    def stop(self) -> None:
        self._stop.set()
        if self._t is not None:
            self._t.join(3)
        self._t = None
        self.tracks.clear()
        self.stats.state, self.stats.speaker, self.stats.activity = "stopped", None, {}

    def speaker(self) -> str | None:
        return self.stats.speaker if self.running else None

    def _models(self):
        if self.detector_factory is not None and self.mesh_factory is not None:
            return self.detector_factory(), self.mesh_factory()
        from vcstudio.ai import face_mesh
        from vcstudio.ai.onnx_engine import YuNetFaceDetector, yunet_path
        if not yunet_path().exists() or not face_mesh.available():
            return None, None
        return YuNetFaceDetector(), face_mesh.FaceMesh()

    def _run(self, stop: threading.Event) -> None:
        try:
            det, mesh = self._models()
        except Exception as e:
            self.stats.state, self.stats.error = "error", f"{type(e).__name__}: {e}"
            log.warning("speaker detection: models did not load: %s", self.stats.error)
            return
        if det is None:
            self.stats.state = "no_models"
            return
        self.stats.state, self.stats.error = "running", ""
        while not stop.is_set():
            t0 = time.monotonic()
            try:
                self.step(det, mesh, t0)
            except Exception as e:
                self.stats.error = f"{type(e).__name__}: {e}"
                log.exception("speaker detection step failed")
            stop.wait(max(0.0, 1.0 / self.fps - (time.monotonic() - t0)))

    def measure(self, frame, det, mesh) -> float | None:
        """Mouth opening of the largest face in `frame` (a vcstudio Frame), None without a face."""
        from vcstudio.core.frame import Frame
        with frame.bgra() as a:
            h, w = a.shape[:2]
            s = min(1.0, DETECT_SIDE / max(h, w))
            small = cv2.resize(a, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA) \
                if s < 1 else np.ascontiguousarray(a)
            bgr = np.ascontiguousarray(a[..., :3])
        faces, _ = det.detect(Frame(0, 0.0, 0.0, small.shape[1], small.shape[0], array=small, source="speaker"))
        faces = [f for f in faces if f.points is not None]
        if not faces:
            return None
        f = max(faces, key=lambda d: (d.x1 - d.x0) * (d.y1 - d.y0))
        box = (f.x0 / s, f.y0 / s, f.x1 / s, f.y1 / s)
        m = mesh.run(bgr, box, np.asarray(f.points, np.float32) / s)
        return None if m is None else mouth_open(m.points)

    def step(self, det, mesh, now: float) -> None:
        """One pass over all sources (also called directly by tests)."""
        t = time.perf_counter()
        frames = self.frames()
        for sid in list(self.tracks):
            if sid not in frames:
                del self.tracks[sid]
        for sid, fr in frames.items():
            tr = self.tracks.setdefault(sid, MouthTrack())
            ver = getattr(fr, "seq", None)
            if fr is None or (ver is not None and self._versions.get(sid) == ver):
                tr.add(now, None)
                continue
            self._versions[sid] = ver
            tr.add(now, self.measure(fr, det, mesh))
        act = {sid: round(tr.activity(now), 4) for sid, tr in self.tracks.items()}
        st = self.stats
        st.activity, st.speaker = act, pick_speaker(act)
        st.runs += 1
        st.ms = (time.perf_counter() - t) * 1000
