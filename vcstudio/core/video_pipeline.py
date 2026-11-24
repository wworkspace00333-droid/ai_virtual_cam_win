"""Real-time processing pipeline: newest captured frame -> tracked regions -> compositor -> outputs.

One thread, paced by capture (one output per new input frame, newest frame wins; nothing queues).
AI runs on its own thread; here we only *query* the tracker for boxes predicted to this frame's
timestamp, so AI latency never delays output.

Output modes
  normal       Metal (or CPU) compositing of the active profile's replacements
  passthrough  input pixel buffer forwarded untouched (zero-copy) — emergency switch, automatic when
               compositing keeps failing, and the natural path when no replacement is active
  paused       the last output frame is re-sent (clients see a frozen image; capture continues)

Outputs: the virtual camera (CMIO sink; reconnects automatically) and a (input, output) pair slot
for synchronised original/processed previews.
"""
from __future__ import annotations

import collections
import copy
import logging
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from vcstudio.core.frame import Frame, host_time_s
from vcstudio.core.frame_queue import LatestSlot
from vcstudio.core.performance import FpsCounter

log = logging.getLogger(__name__)

AUTO_PASSTHROUGH_ERRORS = 5          # consecutive compositor failures before automatic passthrough
CAMERA_RETRY_S = 3.0


@dataclass
class PipelineStats:
    state: str = "stopped"            # stopped | running | paused | passthrough
    output_fps: float = 0.0
    frames_out: int = 0
    inputs_skipped: int = 0           # newer input arrived before the previous one was processed
    latency_ms: float = 0.0           # capture arrival -> output ready (EMA)
    latency_ms_p50: float = 0.0       # over the last 240 frames
    latency_ms_p95: float = 0.0
    process_ms: float = 0.0           # per-frame processing time (EMA)
    render_ms: float = 0.0
    gpu_ms: float = 0.0
    backend: str = ""
    regions: int = 0
    passthrough_reason: str = ""
    render_errors: int = 0
    camera_state: str = "disabled"    # disabled | waiting (no frame sent yet) | not_installed | connected | error
    camera_device: str = ""
    camera_sent: int = 0
    camera_dropped: int = 0
    camera_error: str = ""
    restarts: int = 0
    last_error: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


class CameraOutputController:
    """Owns the virtual camera connection: lazy open, retry with backoff, reconnect after errors.

    Thread-safe: send() (pipeline thread, possibly also a thread abandoned by a watchdog restart) and
    configure() / close() / shutdown() (GUI thread) are serialised by one lock. After shutdown() a late send()
    from a pipeline thread that is still finishing its frame does not reopen the camera; resume() allows it again."""

    def __init__(self, device: str, sink: str, enabled: bool = True, pixel_format: str = "BGRA"):
        self.device, self.sink, self.enabled = device, sink, enabled
        self.pixel_format = pixel_format
        self._out: Any = None             # VirtualCameraOutput once opened
        self._next_try = 0.0
        self._lock = threading.RLock()
        self._shut = False
        self.state = "disabled" if not enabled else "waiting"
        self.error = ""

    def configure(self, device: str, sink: str, enabled: bool, pixel_format: str | None = None) -> None:
        pixel_format = pixel_format or self.pixel_format
        with self._lock:
            if (device, sink, enabled, pixel_format) != (self.device, self.sink, self.enabled, self.pixel_format):
                self.close()
                self.device, self.sink, self.enabled, self.pixel_format = device, sink, enabled, pixel_format
                self._next_try = 0.0
                self.state = "disabled" if not enabled else "waiting"

    def shutdown(self) -> None:
        """Close and refuse sends until resume() (pipeline stopped)."""
        with self._lock:
            self._shut = True
            self.close()

    def resume(self) -> None:
        with self._lock:
            self._shut = False

    def send(self, pb, pts: float) -> bool | None:
        with self._lock:
            if self._shut:
                return None
            return self._send(pb, pts)

    def _send(self, pb, pts: float) -> bool | None:
        if not self.enabled:
            self.state = "disabled"
            return None
        if self._out is None:
            if time.monotonic() < self._next_try:
                return None
            from vcstudio.camera.output import CameraOutputError, VirtualCameraOutput
            try:
                out = VirtualCameraOutput(self.device, self.sink, self.pixel_format)
                out.open()
                self._out = out
                self.state, self.error = "connected", ""
            except CameraOutputError as e:
                self.state = "not_installed" if e.code in (-1, -2) else "error"
                self.error = str(e)
                self._next_try = time.monotonic() + CAMERA_RETRY_S
                return None
        try:
            return self._out.send(pb, pts)
        except Exception as e:
            log.warning("Camera output error, reconnecting: %s", e)
            self.error = str(e)
            self.state = "error"
            self.close()
            self._next_try = time.monotonic() + 0.5
            return None

    def stats(self) -> tuple[int, int]:
        out = self._out
        return out.stats() if out else (0, 0)

    def close(self) -> None:
        with self._lock:
            if self._out is not None:
                try:
                    self._out.close()
                except Exception:
                    pass
                self._out = None


class VideoPipeline:
    def __init__(self, capture, ai, rois, replacement, camera: CameraOutputController,
                 event_sink=None):
        self.capture, self.ai, self.rois, self.replacement, self.camera = capture, ai, rois, replacement, camera
        self._sink = event_sink or (lambda lv, c, m: None)
        self.processed: LatestSlot[tuple[Frame, Frame]] = LatestSlot()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._stats = PipelineStats()
        self._fps = FpsCounter(1.0)
        self._lat: collections.deque[float] = collections.deque(maxlen=240)
        self.paused = False
        self.passthrough = False
        self.passthrough_reason = ""
        self.replacement_enabled = True
        self.heartbeat = 0.0              # monotonic time of the last loop iteration (watchdog)
        self._last_out: Frame | None = None
        self.override = None              # callable(w, h) -> BGRA array sent instead of the video (calibration)
        self.background = None            # BackgroundService (vcstudio/replacement/background.py), set by the app
        self.scene = None                 # SceneState (vcstudio/core/scene.py): published whole by the app
        from vcstudio.replacement.framing import AutoFramer
        from vcstudio.replacement.lighting import AutoLight
        self.framer = AutoFramer()        # auto-framing: crop to the people, follow them smoothly
        self.light = AutoLight()          # automatic exposure / white balance of the camera picture
        self.relit = False                # virtual lights drew on the last frame (status)
        from vcstudio.replacement.camera_motion import HandheldMotion
        self.motion = HandheldMotion()    # AI handheld camera motion (vcstudio/replacement/camera_motion.py)
        from vcstudio.core.scene_transition import SceneTransition
        self.transition = SceneTransition()   # fade / slide / dip between scenes (vcstudio/core/scene_transition.py)
        from vcstudio.replacement.color import ColorGrader
        self.grader = ColorGrader()       # Colour Studio: grading and LUTs of the scene, once per frame
        self.plugins = None               # PluginManager (vcstudio/plugins): user effects on the finished output
        self.multicam = None              # MulticamService (vcstudio/core/multicam.py): extra cameras and layouts
        self.vision = None                # VisionService (vcstudio/ai/vision.py): landmark / pose overlays
        self.shield = None                # PrivacyShield (vcstudio/replacement/privacy_shield.py): text / areas hidden
        self.taps: list = []              # callables(bgra, pts) given every output frame (recorder, instant replay);
        #                                   they must only queue the frame — they run on the output thread
        self._override_cache: tuple | None = None
        self._consecutive_errors = 0
        self._generation = 0

    # ------------------------------------------------------------- control
    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._generation += 1
        gen = self._generation
        self.camera.resume()
        self.heartbeat = time.monotonic()
        self._thread = threading.Thread(target=self._run, args=(gen,), name="video-pipeline", daemon=True)
        self._thread.start()
        self._event("INFO", "started")

    def stop(self) -> None:
        self._stop.set()
        self._generation += 1             # a thread that outlives the join below ends at its next check
        t = self._thread
        if t is not None:
            t.join(timeout=3)
            if t.is_alive():
                log.warning("Pipeline thread did not stop within 3 s; the camera is closed anyway")
        self._thread = None
        self.camera.shutdown()            # waits for a send in progress; later sends are refused
        self.processed.clear()
        self._last_out = None
        with self._lock:
            self._stats.state = "stopped"
        self._event("INFO", "stopped")

    def restart(self, reason: str) -> None:
        """Watchdog recovery: abandon a stuck thread (daemon) and start a fresh one."""
        self._event("WARNING", f"restart: {reason}")
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)
        self._thread = None
        with self._lock:
            self._stats.restarts += 1
        self.start()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        self._event("INFO", "output paused" if paused else "output resumed")

    def set_passthrough(self, on: bool, reason: str = "manual") -> None:
        self.passthrough = on
        self.passthrough_reason = reason if on else ""
        self._consecutive_errors = 0
        self._event("WARNING" if on else "INFO", f"passthrough {'ON (' + reason + ')' if on else 'OFF'}")

    def set_replacement_enabled(self, on: bool) -> None:
        self.replacement_enabled = on
        self._event("INFO", f"replacement {'enabled' if on else 'disabled'}")

    def output_fps(self) -> float:
        """Frames sent per second (1 s window); cheap, for the AI engine's adaptive rate."""
        return self._fps.rate()

    def stats(self) -> PipelineStats:
        with self._lock:
            s = copy.copy(self._stats)
        s.output_fps = self._fps.rate()
        if self._lat:
            lat = sorted(self._lat)
            s.latency_ms_p50 = lat[(len(lat) - 1) // 2]
            s.latency_ms_p95 = lat[int(0.95 * (len(lat) - 1))]
        s.camera_state, s.camera_error, s.camera_device = self.camera.state, self.camera.error, self.camera.device
        s.camera_sent, s.camera_dropped = self.camera.stats()
        if s.state != "stopped":
            s.state = "paused" if self.paused else ("passthrough" if self.passthrough else "running")
        s.passthrough_reason = self.passthrough_reason
        return s

    def _event(self, level: str, msg: str) -> None:
        getattr(log, level.lower())("Pipeline: %s", msg)
        self._sink(level, "pipeline", msg)

    # ------------------------------------------------------------- loop
    def _run(self, gen: int) -> None:
        from vcstudio.platform import autorelease_pool
        st = {"last_ver": -1}
        with self._lock:
            self._stats.state = "running"
        while not self._stop.is_set() and gen == self._generation:
            # macOS: drain Objective-C autoreleased objects (CMSampleBuffers, Metal objects) every frame
            with autorelease_pool():
                self._iteration(st)
        self.heartbeat = time.monotonic()

    def _light(self, frame: Frame, lcfg) -> Frame:
        """Lighting enhancement (vcstudio/replacement/lighting.py); any failure leaves the picture as it was."""
        try:
            self.light.cfg = lcfg
            faces = [(o.x0, o.y0, o.x1, o.y1) for o in self.ai.tracked_at(frame.pts) if o.label == "face"]
            with frame.bgra() as a:
                self.light.update(a, faces, frame.pts)
                lit = self.light.apply(a)
                if lit is a:
                    return frame
            return Frame(frame.seq, frame.pts, frame.arrival, frame.width, frame.height, array=lit,
                         source=f"{frame.source}+light")
        except Exception as e:
            with self._lock:
                self._stats.last_error = f"lighting: {type(e).__name__}: {e}"
            log.exception("lighting failed; frame passed unchanged")
            return frame

    def _relight(self, work: Frame, lcfg, frame: Frame) -> Frame:
        """Virtual studio lights (vcstudio/replacement/virtual_light.py) aimed at the largest face, on the person
        mask when background segmentation runs; no face or any failure: the picture as it was."""
        from vcstudio.replacement.virtual_light import relight
        try:
            faces = [(o.x0, o.y0, o.x1, o.y1) for o in self.ai.tracked_at(frame.pts) if o.label == "face"]
            vs = self.vision
            if not faces and vs is not None and vs.cfg.enabled:
                r = vs.result_for(frame.pts)
                faces = [f.box for f in (r.faces if r is not None else [])]
            if not faces:
                self.relit = False
                return work
            face = max(faces, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
            person = None
            bg = self.background
            if bg is not None and bg.seg.running:
                person = bg.seg.masks.peek()[0]
            with work.bgra() as a:
                out = relight(a, face, lcfg.rig(), person)
                if out is a:
                    return work
            self.relit = True
            return Frame(work.seq, work.pts, work.arrival, work.width, work.height, array=out,
                         source=f"{work.source}+lights")
        except Exception as e:
            with self._lock:
                self._stats.last_error = f"virtual lights: {type(e).__name__}: {e}"
            log.exception("virtual lighting failed; frame passed unchanged")
            return work

    def _grade(self, frame: Frame, ccfg) -> Frame:
        """Colour Studio (vcstudio/replacement/color.py); any failure leaves the picture as it was."""
        try:
            with frame.bgra() as a:
                fps = self.capture.stats().nominal_fps or 30.0
                res = self.grader.apply(a, ccfg, frame.pts, budget_ms=500.0 / max(1.0, fps))   # half a frame
                if res is a:
                    return frame
            return Frame(frame.seq, frame.pts, frame.arrival, frame.width, frame.height, array=res,
                         source=f"{frame.source}+color")
        except Exception as e:
            self.grader.error = f"{type(e).__name__}: {e}"
            with self._lock:
                self._stats.last_error = f"colour: {type(e).__name__}: {e}"
            log.exception("colour grading failed; frame passed unchanged")
            return frame

    def _run_plugins(self, pm, out: Frame, frame: Frame, sc) -> Frame:
        """User plugins on the finished picture (after framing, before the camera). The manager guards each call;
        anything else going wrong here leaves the frame as it was."""
        from vcstudio.core.pixelbuffer import pixel_buffer_for
        from vcstudio.plugins import FrameInfo, PluginObject
        try:
            sx, sy = out.width / max(1, frame.width), out.height / max(1, frame.height)
            objs = tuple(PluginObject(o.label, o.track_id, float(o.confidence),
                                      (o.x0 * sx, o.y0 * sy, o.x1 * sx, o.y1 * sy))
                         for o in self.ai.tracked_at(frame.pts))
            info = FrameInfo(frame.pts, out.width, out.height, getattr(sc, "name", "") or "", objs)
            with out.bgra() as a:
                res = pm.run(a, info)
                if res is a:
                    return out
            arr = Frame(out.seq, out.pts, out.arrival, out.width, out.height, array=res, source=out.source)
            return Frame(out.seq, out.pts, out.arrival, out.width, out.height, pixel_buffer=pixel_buffer_for(arr),
                         source=f"{out.source}+plugins")
        except Exception as e:
            with self._lock:
                self._stats.last_error = f"plugins: {type(e).__name__}: {e}"
            log.exception("plugins failed; frame passed unchanged")
            return out

    def _shield(self, sh, out: Frame) -> Frame:
        """Privacy shield: hide text boxes and the user's areas (frame coordinates, before framing and layouts).
        In strict mode a failure here hides the whole picture rather than showing it unprotected."""
        from vcstudio.core.pixelbuffer import pixel_buffer_for
        try:
            with out.bgra() as a:
                res = sh.apply(a)
                if res is a:
                    return out
            f = Frame(out.seq, out.pts, out.arrival, out.width, out.height, array=res, source=out.source)
            return Frame(out.seq, out.pts, out.arrival, out.width, out.height, pixel_buffer=pixel_buffer_for(f),
                         source=f"{out.source}+shield")
        except Exception as e:
            with self._lock:
                self._stats.last_error = f"privacy shield: {type(e).__name__}: {e}"
            log.exception("privacy shield failed")
            if not sh.cfg.strict:
                return out
            dark = np.zeros((out.height, out.width, 4), np.uint8)
            dark[..., 3] = 255
            f = Frame(out.seq, out.pts, out.arrival, out.width, out.height, array=dark, source=out.source)
            return Frame(out.seq, out.pts, out.arrival, out.width, out.height, pixel_buffer=pixel_buffer_for(f),
                         source=f"{out.source}+shield-failsafe")

    def _vision_overlay(self, vs, out: Frame, frame: Frame) -> Frame:
        """Face points, body and hand skeletons (vcstudio/ai/vision.py) drawn in frame coordinates, before
        auto-framing crops the picture. A result older than STALE_S is not drawn; any failure leaves the frame."""
        from vcstudio.core.pixelbuffer import pixel_buffer_for
        try:
            r = vs.result_for(frame.pts)
            if r is None or not (r.faces or r.poses or r.hands):
                return out
            with out.bgra() as a:
                arr = np.array(a, copy=True)          # never draw into the capture frame or a pooled buffer
            from vcstudio.ai.vision import draw
            draw(arr, r)
            f = Frame(out.seq, out.pts, out.arrival, out.width, out.height, array=arr, source=out.source)
            return Frame(out.seq, out.pts, out.arrival, out.width, out.height, pixel_buffer=pixel_buffer_for(f),
                         source=f"{out.source}+vision")
        except Exception as e:
            with self._lock:
                self._stats.last_error = f"vision overlay: {type(e).__name__}: {e}"
            log.exception("vision overlay failed; frame passed unchanged")
            return out

    def _eye_contact(self, vs, out: Frame, frame: Frame) -> Frame:
        """Eye contact correction (vcstudio/replacement/eye_contact.py) from the newest face mesh, only when it is
        fresh (MAX_AGE_S); any failure or doubt leaves the picture as it was."""
        from vcstudio.core.pixelbuffer import pixel_buffer_for
        from vcstudio.replacement import eye_contact as ec
        try:
            r, _ = vs.slot.peek()
            if r is None or abs(frame.pts - r.pts) > ec.MAX_AGE_S or (r.width, r.height) != (out.width, out.height):
                vs.eye_skipped = vs.eye_skipped + 1
                return out
            faces = [f for f in r.faces if f.mesh is not None and f.gaze is not None]
            if not faces:
                return out
            with out.bgra() as a:
                arr = np.array(a, copy=True)
            n = sum(ec.correct(arr, f.mesh.points, f.gaze, vs.cfg.eye_strength) for f in faces)
            vs.eyes_corrected = n
            if not n:
                return out
            f = Frame(out.seq, out.pts, out.arrival, out.width, out.height, array=arr, source=out.source)
            return Frame(out.seq, out.pts, out.arrival, out.width, out.height, pixel_buffer=pixel_buffer_for(f),
                         source=f"{out.source}+eyes")
        except Exception as e:
            with self._lock:
                self._stats.last_error = f"eye contact: {type(e).__name__}: {e}"
            log.exception("eye contact correction failed; frame passed unchanged")
            return out

    def _camera_motion(self, out: Frame, frame: Frame, mocfg, fcfg) -> Frame:
        """Handheld camera motion of the finished (framed) picture; tracked boxes are mapped into the framed picture
        for the object-aware pivot. Any failure: the picture unmoved."""
        from vcstudio.core.pixelbuffer import pixel_buffer_for
        mo = self.motion
        try:
            tracked = []
            if mocfg.object_aware:
                tracked = list(self.ai.tracked_at(frame.pts))
                r = self.framer.rect if fcfg is not None and fcfg.enabled else None
                if r is not None and tracked:
                    from types import SimpleNamespace
                    x, y, w, h = r
                    sx, sy = frame.width / max(1e-6, w), frame.height / max(1e-6, h)
                    tracked = [SimpleNamespace(label=o.label, x0=(o.x0 - x) * sx, y0=(o.y0 - y) * sy,
                                               x1=(o.x1 - x) * sx, y1=(o.y1 - y) * sy) for o in tracked]
            with out.bgra() as a:
                res = mo.process(a, mocfg, frame.pts, tracked)
                if res is a:
                    return out
            arr = Frame(out.seq, out.pts, out.arrival, out.width, out.height, array=res, source=out.source)
            return Frame(out.seq, out.pts, out.arrival, out.width, out.height, pixel_buffer=pixel_buffer_for(arr),
                         source=f"{out.source}+motion")
        except Exception as e:
            mo.stats.error = f"{type(e).__name__}: {e}"
            with self._lock:
                self._stats.last_error = f"camera motion: {type(e).__name__}: {e}"
            log.exception("camera motion failed; frame passed unmoved")
            return out

    def _scene_transition(self, out: Frame, sc) -> Frame:
        """Blend from the last output of the previous scene into this scene (vcstudio/core/scene_transition.py).
        Passthrough or any failure: the new scene's picture as it is."""
        from vcstudio.core.pixelbuffer import pixel_buffer_for
        tr = self.transition
        prev = self._last_out

        def previous():
            if prev is None:
                return None
            try:
                with prev.bgra() as p:
                    return p.copy()
            except Exception:
                return None
        try:
            kind = "cut" if self.passthrough else sc.transition
            with out.bgra() as a:
                res = tr.process(a, sc.entry, kind, sc.transition_s, time.monotonic(), previous)
                if res is a:
                    return out
            arr = Frame(out.seq, out.pts, out.arrival, out.width, out.height, array=res, source=out.source)
            return Frame(out.seq, out.pts, out.arrival, out.width, out.height, pixel_buffer=pixel_buffer_for(arr),
                         source=f"{out.source}+transition")
        except Exception as e:
            tr.reset()
            with self._lock:
                self._stats.last_error = f"scene transition: {type(e).__name__}: {e}"
            log.exception("scene transition failed; new scene shown directly")
            return out

    def _compose_cameras(self, mc, out: Frame, frame: Frame, mcfg) -> Frame:
        """Multi-camera layout on the finished picture (vcstudio/core/multicam.py). Extra sources are only peeked:
        a missing or slow camera never delays this frame. Any failure leaves the frame as it was."""
        from vcstudio.core.pixelbuffer import pixel_buffer_for
        try:
            people = sum(1 for o in self.ai.tracked_at(frame.pts) if o.label == "person")
            with out.bgra() as a:
                res = mc.process(a, mcfg, people)
                if res is a:
                    return out
            arr = Frame(out.seq, out.pts, out.arrival, out.width, out.height, array=res, source=out.source)
            return Frame(out.seq, out.pts, out.arrival, out.width, out.height, pixel_buffer=pixel_buffer_for(arr),
                         source=f"{out.source}+multicam")
        except Exception as e:
            mc.stats.errors += 1
            mc.stats.last_error = f"{type(e).__name__}: {e}"
            with self._lock:
                self._stats.last_error = f"multicam: {type(e).__name__}: {e}"
            log.exception("multi-camera composition failed; frame passed unchanged")
            return out

    def _frame_to_people(self, out: Frame, frame: Frame, fcfg) -> Frame:
        """Auto-framing: crop the finished output to the people and scale it back (vcstudio/replacement/framing.py).
        Any failure leaves the frame unframed: the camera never waits."""
        from vcstudio.core.pixelbuffer import pixel_buffer_for
        try:
            self.framer.cfg = fcfg
            tracked = list(self.ai.tracked_at(frame.pts))
            vs = self.vision
            if vs is not None and vs.cfg.enabled and not any(o.label in ("person", "face") for o in tracked):
                r = vs.result_for(frame.pts)              # detector off or blind: frame by pose / face points
                if r is not None:
                    from vcstudio.ai.vision import framing_objects
                    tracked += framing_objects(r)
            rect = self.framer.update(tracked, frame.width, frame.height, frame.pts)
            with out.bgra() as a:
                framed = self.framer.apply(a, rect)
                if framed is a:
                    return out
                framed = np.ascontiguousarray(framed)
            arr = Frame(out.seq, out.pts, out.arrival, out.width, out.height, array=framed, source=out.source)
            return Frame(out.seq, out.pts, out.arrival, out.width, out.height, pixel_buffer=pixel_buffer_for(arr),
                         source=f"{out.source}+framed")
        except Exception as e:
            with self._lock:
                self._stats.last_error = f"framing: {type(e).__name__}: {e}"
            log.exception("auto-framing failed; frame passed unframed")
            return out

    def _iteration(self, st: dict) -> None:
        from vcstudio.core.pixelbuffer import pixel_buffer_for
        last_ver = st["last_ver"]
        self.heartbeat = time.monotonic()
        frame, ver = self.capture.slot.wait_newer(last_ver, timeout=0.25)
        if frame is None or ver == last_ver:
            return
        if last_ver >= 0 and ver - last_ver > 1:
            with self._lock:
                self._stats.inputs_skipped += ver - last_ver - 1
        st["last_ver"] = ver
        t0 = time.perf_counter()
        regions_n = 0
        try:
            ov = self.override
            if ov is not None:
                key = (id(ov), frame.width, frame.height)
                if self._override_cache is None or self._override_cache[0] != key:
                    self._override_cache = (key, np.ascontiguousarray(ov(frame.width, frame.height)))
                arr = self._override_cache[1]
                out = Frame(frame.seq, frame.pts, frame.arrival, frame.width, frame.height,
                            pixel_buffer=pixel_buffer_for(Frame(frame.seq, frame.pts, frame.arrival, frame.width,
                                                                frame.height, array=arr, source="override")),
                            source="override")
            elif self.paused and self._last_out is not None:
                out = self._last_out
            else:
                rendered: Frame | None = None
                work = frame                  # the frame the outputs are made from (CPU background applied)
                pre_ops: list = []            # GPU background layers, drawn by the compositor before the regions
                # One scene for the whole frame (vcstudio/core/scene.py): a switch published while this frame is drawn
                # takes effect on the next frame, never half-way through this one.
                sc = self.scene
                lcfg = sc.lighting if sc is not None else None
                if lcfg is not None and lcfg.enabled and not self.passthrough:
                    work = self._light(frame, lcfg)          # correct the camera picture before anything is drawn
                elif self.light._t is not None:
                    self.light.reset()
                if lcfg is not None and lcfg.vl_enabled and not self.passthrough:
                    work = self._relight(work, lcfg, frame)   # virtual key / fill / rim on the person
                ccfg = sc.color if sc is not None else None
                if ccfg is not None and ccfg.active and not self.passthrough:
                    work = self._grade(work, ccfg)           # always from this frame's own pixels: never twice
                bg = self.background
                snap = (sc.bg_cfg, sc.bg_image) if sc is not None and sc.bg_cfg is not None else None
                if bg is not None and not self.passthrough and self.replacement_enabled:
                    try:
                        work = bg.despill(work, snap)            # chroma key only: no green rim on the person
                        if bg.use_gpu_layers(self.replacement.compositor.active.name):
                            pre_ops = bg.ops(work, snap)
                        else:
                            work = bg.apply(work, snap)
                    except Exception as e:    # the frame goes on without background; the camera never waits
                        with self._lock:
                            self._stats.last_error = f"background: {type(e).__name__}: {e}"
                        log.exception("background failed; frame passed without it")
                active = sc.replacement_active if sc is not None else self.replacement.active()
                regions_on = not self.passthrough and self.replacement_enabled and active
                if regions_on or pre_ops:
                    try:
                        regions = []
                        if regions_on:
                            tracked = self.ai.tracked_at(frame.pts)
                            if sc is not None:
                                from vcstudio.roi.resolver import resolve
                                regions = resolve(list(sc.rois), tracked, frame.width, frame.height, frame.pts)
                            else:
                                regions = self.rois.resolve(tracked, frame.width, frame.height, frame.pts)
                        regions_n = len(regions)
                        pb = self.replacement.render_pb(work, regions, frame.pts, pre_ops,
                                                        state=(sc.specs, sc.assets) if sc is not None else None)
                        rendered = Frame(frame.seq, frame.pts, frame.arrival, frame.width, frame.height,
                                         pixel_buffer=pb, source="processed" if regions_on else "background")
                        self._consecutive_errors = 0
                    except Exception as e:
                        self._consecutive_errors += 1
                        with self._lock:
                            self._stats.render_errors += 1
                            self._stats.last_error = f"{type(e).__name__}: {e}"
                        log.exception("frame processing failed; passing this frame through")
                        if self._consecutive_errors >= AUTO_PASSTHROUGH_ERRORS:
                            self.set_passthrough(True, f"automatic: {self._consecutive_errors} consecutive render failures")
                # nothing drawn: forward the (CPU background) frame; passthrough: the input buffer itself (zero-copy)
                out = rendered if rendered is not None else Frame(
                    frame.seq, frame.pts, frame.arrival, frame.width, frame.height,
                    pixel_buffer=pixel_buffer_for(work), source="passthrough" if work is frame else "background")
                vs = self.vision
                if vs is not None and vs.cfg.enabled and vs.cfg.eye_contact and not self.passthrough:
                    out = self._eye_contact(vs, out, frame)           # before the shield: hidden faces stay hidden
                sh = self.shield
                if sh is not None and sh.cfg.enabled and (not self.passthrough or sh.cfg.strict):
                    out = self._shield(sh, out)
                vs = self.vision
                if vs is not None and vs.cfg.enabled and vs.cfg.overlay and not self.passthrough:
                    out = self._vision_overlay(vs, out, frame)
                fcfg = sc.framing if sc is not None else None
                if fcfg is not None and fcfg.enabled and not self.passthrough:
                    out = self._frame_to_people(out, frame, fcfg)
                elif self.framer.rect is not None:
                    self.framer.reset()
                mocfg = sc.motion if sc is not None else None
                if mocfg is not None and (mocfg.enabled or self.motion.active) and not self.passthrough:
                    out = self._camera_motion(out, frame, mocfg, fcfg)   # handheld camera on the framed picture
                mc = self.multicam
                mcfg = sc.multicam if sc is not None else None
                if mc is not None and mcfg is not None and mcfg.enabled and not self.passthrough:
                    out = self._compose_cameras(mc, out, frame, sc.multicam)
                pm = self.plugins
                if pm is not None and pm.active and not self.passthrough:
                    out = self._run_plugins(pm, out, frame, sc)
                if sc is not None and not self.transition.idle_for(sc.entry):
                    out = self._scene_transition(out, sc)      # the whole finished picture blends between scenes
                if sc is not None:
                    out.meta["scene"] = sc.version
                self._last_out = out
            self.camera.send(out.pixel_buffer, frame.pts)
            if self.taps and isinstance(out.pixel_buffer, np.ndarray):
                for tap in tuple(self.taps):
                    try:
                        tap(out.pixel_buffer, frame.pts)
                    except Exception:
                        log.exception("output tap failed")
            self.processed.put((frame, out))
            done = host_time_s()
            self._fps.tick()
            ms = (time.perf_counter() - t0) * 1000
            lat = (done - frame.arrival) * 1000
            self._lat.append(lat)
            cs = self.replacement.compositor.stats
            with self._lock:
                ps = self._stats          # (not "st": that is this loop's own state dict)
                ps.frames_out += 1
                ps.process_ms = ms if ps.frames_out == 1 else 0.9 * ps.process_ms + 0.1 * ms
                ps.latency_ms = lat if ps.frames_out == 1 else 0.9 * ps.latency_ms + 0.1 * lat
                ps.render_ms, ps.gpu_ms, ps.backend = cs.render_ms, cs.gpu_ms, cs.backend
                ps.regions = regions_n
        except Exception as e:      # never let the output thread die
            log.exception("pipeline iteration failed")
            with self._lock:
                self._stats.last_error = f"{type(e).__name__}: {e}"
            time.sleep(0.01)
