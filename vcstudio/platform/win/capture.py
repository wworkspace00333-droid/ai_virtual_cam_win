"""Camera capture on Windows (DirectShow or Media Foundation through OpenCV's videoio backends).

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

import cv2
import numpy as np

from vcstudio.core.devices import is_own_output
from vcstudio.core.frame import Frame, host_time_s
from vcstudio.core.video_capture import (CaptureError, CaptureSource, DeviceFormat, FeedbackLoopRefused,
                                         PermissionDenied, choose_format)
from vcstudio.platform.win import devices as wd

log = logging.getLogger(__name__)

BACKENDS = {"directshow": cv2.CAP_DSHOW, "mediafoundation": cv2.CAP_MSMF}
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
        exact = [d for d in devs if d.name.casefold() == name_hint.casefold()]
        part = [d for d in devs if name_hint.casefold() in d.name.casefold()]
        return (exact or part or [None])[0]
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
        self._cap: cv2.VideoCapture | None = None
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
        cap = cv2.VideoCapture(dev.index, BACKENDS[self.method])
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
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"capture-{self.method}", daemon=True)
        self._thread.start()
        log.info("%s capture started: %s (index %d) %dx%d @ %.2f fps", self.method, dev.name, dev.index, aw, ah, afps)

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=3)
        self._thread = None
        cap, self._cap = self._cap, None
        if cap is not None:
            cap.release()
            log.info("%s capture stopped: %s", self.method, self.name)
        self._on_frame = None

    # ------------------------------------------------------------------ capture thread
    def _run(self) -> None:
        cap = self._cap
        pool = FramePool()
        seq, fails = 0, 0
        while not self._stop.is_set() and cap is not None:
            ok, img = cap.read()
            if not ok or img is None:
                fails += 1
                if fails >= READ_FAILS_BEFORE_DISCONNECT:
                    cb = self._on_event
                    if cb:
                        cb("disconnected", f"{self.name} stopped delivering frames")
                    return
                self._stop.wait(0.01)
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
