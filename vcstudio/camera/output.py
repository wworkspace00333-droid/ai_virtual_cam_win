"""Send processed frames to the "AI Virtual Camera" system extension (CMIO sink stream).

Thin wrapper over libVCSNative's CMIO sink client. Frames must be IOSurface-backed BGRA
CVPixelBuffers (the Metal compositor produces exactly that), so nothing is copied on the way.
"""
from __future__ import annotations

import ctypes
import logging

from vcstudio import CAMERA_NAME

log = logging.getLogger(__name__)
SINK_NAME = "AI Virtual Camera Input"


class OutputTarget:
    """A CMIO camera with a sink stream that the pipeline can feed."""

    def __init__(self, key: str, label: str, device: str, sink: str, note: str):
        self.key, self.label, self.device, self.sink, self.note = key, label, device, sink, note


# "obs": OBS Studio's own camera extension (signed by OBS, already approved on most Macs that run OBS)
# uses the same sink mechanism. It needs no Apple Developer account, but clients see it as
# "OBS Virtual Camera", and OBS's own Start Virtual Camera must stay off while the app feeds it.
OUTPUT_TARGETS = {
    "ai": OutputTarget("ai", f"{CAMERA_NAME} (this app's camera extension)", CAMERA_NAME, SINK_NAME,
                       f"Other apps select “{CAMERA_NAME}”. Needs the signed camera extension installed."),
    "obs": OutputTarget("obs", "OBS Virtual Camera (no Apple Developer account needed)", "OBS Virtual Camera",
                        "OBS Camera Extension Stream Sink",
                        "Other apps select “OBS Virtual Camera”. Keep OBS's own Start Virtual Camera off, and take "
                        "input with Direct OBS frame transfer (the vcstudio-obs plugin), not from OBS Virtual Camera."),
}

from vcstudio.platform import IS_WINDOWS  # noqa: E402

if IS_WINDOWS:
    # Windows: the app's own camera (DirectShow + Media Foundation, native/win). The sink name is the
    # shared-memory channel the camera DLLs read from.
    OUTPUT_TARGETS = {
        "ai": OutputTarget("ai", f"{CAMERA_NAME} (this app's Windows camera)", CAMERA_NAME, SINK_NAME,
                           f"Other apps select “{CAMERA_NAME}”. Needs the camera components installed "
                           "(the Windows installer registers them)."),
    }


def target_for(device: str, sink: str) -> str:
    """Key of the preset matching device+sink, or "custom"."""
    for k, t in OUTPUT_TARGETS.items():
        if (t.device, t.sink) == (device, sink):
            return k
    return "custom"

OPEN_ERRORS = {
    -1: "the camera device is not installed or not visible (install and approve the camera extension)",
    -2: "the camera device has no sink stream (wrong or outdated extension)",
    -3: "could not open the sink buffer queue",
    -4: "could not start the sink stream",
}


class CameraOutputError(RuntimeError):
    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code = code


def _lib():
    from vcstudio.native import metal
    lib = metal._load()
    if lib is None:
        raise CameraOutputError(-100, f"native library unavailable: {metal.load_error()}")
    if not getattr(lib, "_cam_ready", False):
        lib.vcs_cam_open.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int32)]
        lib.vcs_cam_open.restype = ctypes.c_void_p
        lib.vcs_cam_send.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_double]
        lib.vcs_cam_send.restype = ctypes.c_int32
        lib.vcs_cam_stats.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int64)]
        lib.vcs_cam_close.argtypes = [ctypes.c_void_p]
        lib.vcs_cam_list_devices.argtypes = [ctypes.c_char_p, ctypes.c_int32]
        lib.vcs_cam_list_streams.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int32]
        lib.vcs_cam_set_format.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        lib._cam_ready = True
    return lib


def list_cmio_devices() -> list[str]:
    buf = ctypes.create_string_buffer(8192)
    n = _lib().vcs_cam_list_devices(buf, len(buf))
    return [s for s in buf.value.decode().split("\n") if s] if n > 0 else []


def list_streams(device: str) -> list[str]:
    buf = ctypes.create_string_buffer(8192)
    n = _lib().vcs_cam_list_streams(device.encode(), buf, len(buf))
    return [s for s in buf.value.decode().split("\n") if s] if n > 0 else []


PIXEL_FORMATS = {"BGRA": 0, "NV12": int.from_bytes(b"420v", "big")}   # 0 = send frames as rendered (BGRA)


class VirtualCameraOutput:
    def __init__(self, device: str = CAMERA_NAME, sink: str = SINK_NAME, pixel_format: str = "BGRA"):
        self.device, self.sink = device, sink
        self.pixel_format = pixel_format if pixel_format in PIXEL_FORMATS else "BGRA"
        self._h = None

    @property
    def is_open(self) -> bool:
        return self._h is not None

    def open(self) -> None:
        if self._h:
            return
        status = ctypes.c_int32(0)
        h = _lib().vcs_cam_open(self.device.encode(), self.sink.encode(), ctypes.byref(status))
        if not h:
            raise CameraOutputError(status.value, f"{self.device}: {OPEN_ERRORS.get(status.value, status.value)}")
        self._h = h
        _lib().vcs_cam_set_format(h, PIXEL_FORMATS[self.pixel_format])
        log.info("Virtual camera output opened: %s / %s (%s)", self.device, self.sink, self.pixel_format)

    def send(self, pixel_buffer, pts_seconds: float) -> bool:
        """True if enqueued, False if dropped because the extension's queue was full."""
        if not self._h:
            raise CameraOutputError(-10, "output not open")
        rc = _lib().vcs_cam_send(self._h, pixel_buffer.__c_void_p__().value, pts_seconds)
        if rc < 0:
            raise CameraOutputError(rc, f"send failed ({rc})")
        return rc == 0

    def stats(self) -> tuple[int, int]:
        if not self._h:
            return (0, 0)
        out = (ctypes.c_int64 * 2)()
        _lib().vcs_cam_stats(self._h, out)
        return out[0], out[1]

    def close(self) -> None:
        if self._h:
            _lib().vcs_cam_close(self._h)
            self._h = None
            log.info("Virtual camera output closed")

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


# =============================================================================================
# Windows: frames go to the "AI Virtual Camera" DirectShow / Media Foundation devices through shared
# memory (vcstudio/platform/win/vcam_writer.py). One writer per process, shared by the pipeline and the
# test-pattern sender; the camera converts to whatever format each client negotiated.
# =============================================================================================
if IS_WINDOWS:
    import threading as _threading

    _writer = None
    _writer_users = 0
    _writer_lock = _threading.Lock()

    def _acquire_writer():
        global _writer, _writer_users
        with _writer_lock:
            if _writer is None:
                from vcstudio.platform.win.vcam_writer import VcamWriter
                _writer = VcamWriter()
            _writer_users += 1
            return _writer

    def _release_writer():
        global _writer, _writer_users
        with _writer_lock:
            _writer_users = max(0, _writer_users - 1)
            if _writer_users == 0 and _writer is not None:
                _writer.close()
                _writer = None

    def list_cmio_devices() -> list[str]:          # noqa: F811 - Windows: video capture devices by name
        from vcstudio.core.devices import list_video_devices
        return [d.name for d in list_video_devices()]

    def list_streams(device: str) -> list[str]:     # noqa: F811 - no sink streams on Windows
        return [SINK_NAME] if device == CAMERA_NAME else []

    class VirtualCameraOutput:                      # noqa: F811
        """Windows implementation of the camera output (same interface as the CMIO sink client)."""

        def __init__(self, device: str = CAMERA_NAME, sink: str = SINK_NAME, pixel_format: str = "BGRA"):
            self.device, self.sink, self.pixel_format = device, sink, pixel_format
            self._w = None
            self._sent = 0
            self._dropped = 0
            self._size = None

        @property
        def is_open(self) -> bool:
            return self._w is not None

        def open(self) -> None:
            if self._w is not None:
                return
            if self.device != CAMERA_NAME:
                raise CameraOutputError(-1, f"{self.device}: only “{CAMERA_NAME}” can receive frames on Windows")
            from vcstudio.platform.win.vcam_status import components
            if not any(c.state == "installed" for c in components()):
                raise CameraOutputError(-1, f"{self.device}: {OPEN_ERRORS[-1]}")
            try:
                self._w = _acquire_writer()
            except OSError as e:
                raise CameraOutputError(-3, f"{self.device}: cannot create the frame channel ({e})") from e
            log.info("Virtual camera output opened: %s (shared memory %s)", self.device, self.sink)

        def send(self, pixel_buffer, pts_seconds: float) -> bool:
            import numpy as np
            if self._w is None:
                raise CameraOutputError(-10, "output not open")
            a = np.asarray(pixel_buffer)
            h, w = a.shape[:2]
            if w > 1920 or h > 1080:                 # the camera devices offer up to 1080p
                import cv2
                s = min(1920 / w, 1080 / h)
                a = cv2.resize(a, (int(w * s) // 2 * 2, int(h * s) // 2 * 2), interpolation=cv2.INTER_AREA)
                h, w = a.shape[:2]
            if self._size != (w, h):
                self._w.configure(w, h, 60.0)
                self._size = (w, h)
            self._w.publish(np.ascontiguousarray(a), int(pts_seconds * 1e9))
            self._sent += 1
            return True

        def stats(self) -> tuple[int, int]:
            return self._sent, self._dropped

        def close(self) -> None:
            if self._w is not None:
                self._w = None
                _release_writer()
                log.info("Virtual camera output closed")

        def __del__(self):
            try:
                self.close()
            except Exception:
                pass
