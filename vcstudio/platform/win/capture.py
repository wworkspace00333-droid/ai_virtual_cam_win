"""Camera capture on Windows: DirectShow through OpenCV's videoio backend, Media Foundation through the app's own
reader (vcstudio/platform/win/mfcap.py; the opencv-python wheels have no MSMF backend).

OpenCV opens a device by its index in the backend's own enumeration order; vcstudio/platform/win/devices.py
enumerates in exactly that order, so the saved unique id (device path / filter CLSID) or name is mapped to
the right index at every (re)start: plugging in another camera never makes the app capture the wrong one.

A capture thread reads frames, converts them to BGRA (the pipeline's frame format) into a small pool of
reused buffers and hands them to the supervisor callback; the newest frame wins downstream. The thread
never blocks the pipeline. A failed read is reported as "disconnected" and the supervisor reconnects.

Timestamps: frames are stamped with the host clock (QPC) at arrival. OpenCV does not expose DirectShow
sample times reliably, and arrival time is what latency and AI/tracker prediction need.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

import cv2
import numpy as np

from vcstudio.core.devices import is_own_output
from vcstudio.core.frame import Frame, host_time_s
from vcstudio.core.video_capture import (CaptureError, CaptureSource, DeviceFormat, FeedbackLoopRefused,
                                         PermissionDenied, choose_format)
from vcstudio.platform.win import devices as wd

log = logging.getLogger(__name__)

BACKENDS = {"directshow": cv2.CAP_DSHOW, "mediafoundation": None}      # None: vcstudio.platform.win.mfcap
READ_FAILS_BEFORE_DISCONNECT = 15


class FramePool:
    """Reuses BGRA buffers only when nothing else references them (like CVPixelBufferPool on macOS).

    A frame handed to the pipeline may still be read by the AI thread, a preview or the compositor long
    after newer frames arrived; overwriting it in place would tear those reads. A buffer whose only
    reference is this pool (checked with sys.getrefcount; views keep their base alive) is free. Memory
    stays bounded: at most MAX buffers are pooled, and frames beyond that are plain allocations."""
    MAX = 8

    def __init__(self):
        self._bufs: list[np.ndarray] = []
        self.allocated = 0

    def get(self, h: int, w: int) -> np.ndarray:
        import sys
        if self._bufs and self._bufs[0].shape[:2] != (h, w):
            self._bufs = []
        for b in self._bufs:
            if sys.getrefcount(b) <= 3:          # list entry + loop variable + getrefcount's argument
                return b
        b = np.empty((h, w, 4), np.uint8)
        self.allocated += 1
        if len(self._bufs) < self.MAX:
            self._bufs.append(b)
        return b


def resolve_device(method: str, unique_id: str | None, name_hint: str | None) -> wd.BackendDevice | None:
    devs = wd.backend_devices(method)
    if unique_id:
        for d in devs:
            if d.unique_id == unique_id:
                return d
    if name_hint:
        from vcstudio import CAMERA_NAME
        from vcstudio.core.devices import own_camera_name
        hints = [name_hint]
        if name_hint == CAMERA_NAME and own_camera_name() != CAMERA_NAME:
            hints.insert(0, own_camera_name())       # this app's camera after the user renamed it
        for hint in hints:
            exact = [d for d in devs if d.name.casefold() == hint.casefold()]
            part = [d for d in devs if hint.casefold() in d.name.casefold()]
            if exact or part:
                return (exact or part)[0]
    return None


class WindowsCameraSource(CaptureSource):
    def __init__(self, method: str, unique_id: str | None, name_hint: str | None = None, width: int = 1920,
                 height: int = 1080, fps: float = 60.0, allow_own_output: bool = False):
        if method not in BACKENDS:
            raise CaptureError(f"unknown capture method {method!r}")
        self.kind = method
        self.method, self.unique_id, self.name_hint = method, unique_id, name_hint
        self.want = (width, height, fps)
        self.allow_own_output = allow_own_output
        self.name = name_hint or unique_id or "?"
        self.selected_format: DeviceFormat | None = None
        self._nominal = (0, 0, 0.0)
        self._cap: Any = None               # cv2.VideoCapture (DirectShow) or MfCapture (Media Foundation)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._on_frame = None
        self._on_event = None

    @property
    def nominal(self) -> tuple[int, int, float]:
        return self._nominal

    def is_present(self) -> bool:
        return resolve_device(self.method, self.unique_id, None if self.unique_id else self.name_hint) is not None

    # ------------------------------------------------------------------ start / stop
    def start(self, on_frame, on_event) -> None:
        from vcstudio.core.devices import camera_authorization_status
        dev = resolve_device(self.method, self.unique_id, self.name_hint)
        if dev is None:
            raise CaptureError(f"Video device not found ({'DirectShow' if self.method == 'directshow' else 'Media Foundation'}): "
                               f"{self.name_hint or self.unique_id}")
        self.name, self.unique_id = dev.name, dev.unique_id
        if is_own_output(dev.name, "") and not self.allow_own_output:
            raise FeedbackLoopRefused(f"Refusing to capture from '{dev.name}': it is this app's own output.")
        w, h, fps = self.want
        fmts = []
        try:
            fmts = wd.formats_for_device_id(dev.unique_id)
        except Exception as e:      # formats are a hint; the device may still open
            log.info("formats of %s unavailable: %s", dev.name, e)
        chosen = choose_format(fmts, w, h, fps)
        if chosen is not None:
            w, h = chosen.width, chosen.height
            fps = min(max(fps, chosen.min_fps), chosen.max_fps)
        if self.method == "mediafoundation":
            self._start_mf(dev, w, h, fps, chosen, on_frame, on_event)
            return
        cap = cv2.VideoCapture(dev.index, int(BACKENDS[self.method] or cv2.CAP_ANY))
        if not cap.isOpened():
            cap.release()
            # Windows camera privacy applies to hardware cameras (device path), not to software
            # DirectShow filters such as OBS Virtual Camera, so only blame it for hardware devices.
            hardware = not dev.unique_id.startswith(("dshow-clsid:", "dshow-name:"))
            if hardware and camera_authorization_status() != "authorized":
                raise PermissionDenied("Camera access is turned off in Windows Settings → Privacy & security → Camera.")
            raise CaptureError(f"Cannot open {dev.name} (index {dev.index}, {self.method}): the device refused to "
                               "start (in use by another app, or its driver/filter failed to load). For OBS, Direct OBS "
                               "frame transfer avoids OBS Virtual Camera entirely.")
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        cap.set(cv2.CAP_PROP_FPS, fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)            # newest frame, no driver-side queue where supported
        aw, ah = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        afps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0) or fps
        self.selected_format = chosen or DeviceFormat(aw, ah, "?", afps, afps)
        self._nominal = (aw, ah, afps)
        self._cap = cap
        self._on_frame, self._on_event = on_frame, on_event
        self._launch(self._run, cap, f"capture-{self.method}")
        log.info("%s capture started: %s (index %d) %dx%d @ %.2f fps", self.method, dev.name, dev.index, aw, ah, afps)

    def _start_mf(self, dev, w, h, fps, chosen, on_frame, on_event) -> None:
        from vcstudio.core.devices import camera_authorization_status
        from vcstudio.platform.win.mfcap import MfCapture, MfCaptureError
        try:
            cap = MfCapture(dev.unique_id, w, h, fps)
        except MfCaptureError as e:
            if "access denied" in str(e) or camera_authorization_status() != "authorized":
                raise PermissionDenied(f"{dev.name}: {e}. Turn on camera access in Windows Settings → Privacy & "
                                       "security → Camera.") from e
            raise CaptureError(f"Cannot open {dev.name} (Media Foundation): {e}") from e
        self.selected_format = chosen or DeviceFormat(cap.width, cap.height, cap.subtype, cap.fps, cap.fps)
        self._nominal = (cap.width, cap.height, cap.fps)
        self._cap = cap
        self._on_frame, self._on_event = on_frame, on_event
        self._launch(self._run_mf, cap, "capture-mediafoundation")
        log.info("mediafoundation capture started: %s %dx%d @ %.2f fps (%s)", dev.name, cap.width, cap.height,
                 cap.fps, cap.subtype)

    # The capture thread owns its device: it is the only one that reads it, and it closes it when it ends.
    # stop() never closes a device another thread may still be reading (a DirectShow read can block, and
    # mfcap_close frees what mfcap_read uses); if the thread does not end in time it is abandoned with its
    # own stop event set, and closes the device as soon as its read returns.
    def _launch(self, target, cap, name: str) -> None:
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._owned, args=(target, cap, self._stop), name=name, daemon=True)
        self._thread.start()

    def _owned(self, target, cap, stop: threading.Event) -> None:
        try:
            target(cap, stop)
        finally:
            try:
                cap.close() if self.method == "mediafoundation" else cap.release()
                log.info("%s capture stopped: %s", self.method, self.name)
            except Exception:
                log.exception("closing the capture device failed")

    def _run_mf(self, cap, stop: threading.Event) -> None:
        pool = FramePool()
        seq = 0
        while not stop.is_set():
            out = pool.get(cap.height, cap.width)
            r = cap.read(out, 200)
            if r == cap.TIMEOUT:
                continue                       # the supervisor's missing-input timer judges long gaps
            if r == cap.LOST:
                cb = self._on_event
                if cb:
                    cb("disconnected", f"{self.name} stopped delivering frames (device lost)")
                return
            arrival = host_time_s()
            seq += 1
            cb = self._on_frame
            if cb is None:
                return
            try:
                cb(Frame(seq=seq, pts=arrival, arrival=arrival, width=cap.width, height=cap.height,
                         pixel_format="BGRA", array=out, source=self.name))
            except Exception:
                log.exception("frame callback failed")

    def stop(self) -> None:
        self._stop.set()
        self._on_frame = None                 # an abandoned thread delivers nothing more
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=3)
            if t.is_alive():
                log.warning("%s capture thread did not stop within 3 s; its device is closed when the read returns",
                            self.method)
        self._thread = None
        self._cap = None

    # ------------------------------------------------------------------ capture thread
    def _run(self, cap, stop: threading.Event) -> None:
        pool = FramePool()
        seq, fails = 0, 0
        while not stop.is_set():
            ok, img = cap.read()
            if not ok or img is None:
                fails += 1
                if fails >= READ_FAILS_BEFORE_DISCONNECT:
                    cb = self._on_event
                    if cb:
                        cb("disconnected", f"{self.name} stopped delivering frames")
                    return
                stop.wait(0.01)
                continue
            fails = 0
            arrival = host_time_s()
            h, w = img.shape[:2]
            out = pool.get(h, w)
            if img.ndim == 2:
                cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA, dst=out)
            elif img.shape[2] == 4:
                np.copyto(out, img)
            else:
                cv2.cvtColor(img, cv2.COLOR_BGR2BGRA, dst=out)
            seq += 1
            cb = self._on_frame
            if cb is None:
                return
            try:
                cb(Frame(seq=seq, pts=arrival, arrival=arrival, width=w, height=h, pixel_format="BGRA",
                         array=out, source=self.name))
            except Exception:
                log.exception("frame callback failed")
