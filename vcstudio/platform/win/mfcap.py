"""Media Foundation camera capture through vcs_mfcap.dll (native/win/capture/vcs_mfcap.cpp).

The opencv-python wheels for Windows are built without the MSMF backend (getBuildInformation lists DirectShow
only, checked with 4.12-4.14), so the "Media Foundation" capture method uses this small native reader instead:
an asynchronous IMFSourceReader whose advanced video processor decodes MJPG / converts NV12, YUY2 ... to BGRA
with the colour attributes the device declares. It opens the device by its symbolic link (the unique id
vcstudio/platform/win/devices.py reports), picks the device's own mode nearest to the request, and always hands
out the newest frame.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

import numpy as np

from vcstudio.core.resources import native_lib

_lib = None


def lib():
    global _lib
    if _lib is None:
        p = native_lib("vcs_mfcap.dll")
        if not p.exists():
            raise FileNotFoundError(f"{p} not built (scripts/build_native_win.ps1)")
        L = ctypes.CDLL(str(p))
        L.mfcap_open.restype = ctypes.c_void_p
        L.mfcap_open.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.c_int, ctypes.c_double,
                                 ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
                                 ctypes.POINTER(ctypes.c_double), wintypes.LPWSTR, ctypes.c_int,
                                 wintypes.LPWSTR, ctypes.c_int]
        L.mfcap_read.restype = ctypes.c_int
        L.mfcap_read.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_longlong),
                                 ctypes.c_int]
        L.mfcap_close.restype = None
        L.mfcap_close.argtypes = [ctypes.c_void_p]
        _lib = L
    return _lib


def available() -> bool:
    try:
        lib()
        return True
    except (OSError, FileNotFoundError):
        return False


class MfCaptureError(RuntimeError):
    pass


class MfCapture:
    """One opened Media Foundation device. `read(out)` fills a (h, w, 4) uint8 BGRA array."""

    FRAME, TIMEOUT, LOST = 1, 0, -1

    def __init__(self, symlink: str, width: int, height: int, fps: float):
        L = lib()
        w, h, f = ctypes.c_int(0), ctypes.c_int(0), ctypes.c_double(0)
        sub = ctypes.create_unicode_buffer(32)
        err = ctypes.create_unicode_buffer(512)
        self._h = L.mfcap_open(symlink, int(width), int(height), float(fps), ctypes.byref(w), ctypes.byref(h),
                               ctypes.byref(f), sub, 32, err, 512)
        if not self._h:
            raise MfCaptureError(err.value or "the device could not be opened")
        self.width, self.height, self.fps, self.subtype = w.value, h.value, f.value, sub.value
        self.ts_100ns = 0

    def read(self, out: np.ndarray, timeout_ms: int = 200) -> int:
        if not self._h:
            return self.LOST
        if out.shape != (self.height, self.width, 4) or out.dtype != np.uint8 or not out.flags.c_contiguous:
            raise ValueError("output must be a contiguous (h, w, 4) uint8 array of the opened size")
        ts = ctypes.c_longlong(0)
        r = lib().mfcap_read(self._h, out.ctypes.data, out.nbytes, ctypes.byref(ts), int(timeout_ms))
        if r == -2:
            raise ValueError("frame size mismatch")
        if r == self.FRAME:
            self.ts_100ns = ts.value
        return r

    def close(self) -> None:
        h, self._h = self._h, None
        if h:
            lib().mfcap_close(h)

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
