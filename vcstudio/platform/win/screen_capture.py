"""Screen and window capture on Windows: an input source ("input.method" = "screen") and one-off screenshots.

Three backends, tried in this order, each checked by actually receiving a first frame:

  wgc    Windows Graphics Capture (native/win/capture/vcs_wgc.cpp): monitor or one window, sees GPU-drawn content
         (browsers, games in windowed / borderless mode), captures a window even when it is covered.
  dupl   DXGI Desktop Duplication (vcs_dupl.cpp): whole monitor, GPU-drawn content too; no single window.
  gdi    GDI BitBlt (monitor) / PrintWindow with PW_RENDERFULLCONTENT (window): always available, slower, and an
         exclusive full-screen game may come out black.

An API can exist and still deliver nothing (remote desktop sessions on the basic display adapter: WGC opens but no
frame ever arrives, Desktop Duplication lists no outputs), so "opened" is not trusted: a backend that gives no frame
within FIRST_FRAME_S is closed and the next one is tried. The chosen backend is reported in `backend`.

Targets: "monitor:<index>" (index over EnumDisplayMonitors, 0 = first) or "window:<title>" (first visible top-level
window whose title contains the text, case-insensitive). Frames are BGRA, top-down, the size of the target.
"""
from __future__ import annotations

import ctypes
import logging
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass

import numpy as np

from vcstudio.core.frame import Frame, host_time_s
from vcstudio.core.video_capture import CaptureError, CaptureSource

log = logging.getLogger(__name__)

FIRST_FRAME_S = 1.5
BACKENDS = ("wgc", "dupl", "gdi")
DEFAULT_TARGET = "monitor:0"

_u32 = ctypes.WinDLL("user32", use_last_error=True)
_g32 = ctypes.WinDLL("gdi32", use_last_error=True)
_dwm = ctypes.WinDLL("dwmapi")

_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_MONENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                                  wintypes.LPARAM)
_u32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
_u32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, _MONENUMPROC, wintypes.LPARAM]
_u32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_u32.IsWindowVisible.argtypes = [wintypes.HWND]
_u32.IsWindow.argtypes = [wintypes.HWND]
_u32.IsIconic.argtypes = [wintypes.HWND]
_u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_u32.GetWindowDC.argtypes = [wintypes.HWND]
_u32.GetWindowDC.restype = wintypes.HDC
_u32.GetDC.argtypes = [wintypes.HWND]
_u32.GetDC.restype = wintypes.HDC
_u32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_u32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
_u32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
_u32.GetAncestor.restype = wintypes.HWND
_u32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
_g32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_g32.CreateCompatibleDC.restype = wintypes.HDC
_g32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
_g32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
_g32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
_g32.SelectObject.restype = wintypes.HGDIOBJ
_g32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HDC,
                        ctypes.c_int, ctypes.c_int, wintypes.DWORD]
_g32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                           ctypes.c_void_p, wintypes.UINT]
_g32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_g32.DeleteDC.argtypes = [wintypes.HDC]
_dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]

SRCCOPY, CAPTUREBLT, PW_RENDERFULLCONTENT = 0x00CC0020, 0x40000000, 2
DWMWA_CLOAKED, GA_ROOTOWNER, GWL_EXSTYLE, WS_EX_TOOLWINDOW = 14, 3, -20, 0x00000080


class _BIH(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


# ------------------------------------------------------------------------------------------- targets
@dataclass(frozen=True)
class Monitor:
    index: int
    left: int
    top: int
    width: int
    height: int

    @property
    def label(self) -> str:
        return f"Screen {self.index + 1} ({self.width}×{self.height})"


@dataclass(frozen=True)
class Window:
    hwnd: int
    title: str
    pid: int


def monitors() -> list[Monitor]:
    out: list[Monitor] = []

    def cb(_hm, _hdc, rc, _p):
        r = rc.contents
        out.append(Monitor(len(out), r.left, r.top, r.right - r.left, r.bottom - r.top))
        return True

    _u32.EnumDisplayMonitors(None, None, _MONENUMPROC(cb), 0)
    return out


def primary_index() -> int:
    """Index of the primary monitor (the one at the desktop origin) in monitors()."""
    return next((m.index for m in monitors() if m.left == 0 and m.top == 0), 0)


def _cloaked(hwnd: int) -> bool:
    v = wintypes.DWORD(0)
    return _dwm.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(v), ctypes.sizeof(v)) == 0 and v.value != 0


def windows(include_own: bool = False) -> list[Window]:
    """Visible top-level windows with a title that a person would pick (no tool windows, no hidden UWP frames)."""
    import os
    own = os.getpid()
    out: list[Window] = []

    def cb(hwnd, _p):
        if not _u32.IsWindowVisible(hwnd) or _u32.GetAncestor(hwnd, GA_ROOTOWNER) != hwnd:
            return True
        if _u32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW or _cloaked(hwnd):
            return True
        n = _u32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        _u32.GetWindowTextW(hwnd, buf, n + 1)
        pid = wintypes.DWORD()
        _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if include_own or pid.value != own:
            out.append(Window(int(hwnd), buf.value, pid.value))
        return True

    _u32.EnumWindows(_WNDENUMPROC(cb), 0)
    return out


def parse_target(target: str | None) -> tuple[str, int | str]:
    """("monitor", index) or ("window", title text)."""
    t = (target or DEFAULT_TARGET).strip()
    kind, _, arg = t.partition(":")
    if kind == "window" and arg:
        return "window", arg
    if kind == "monitor":
        try:
            return "monitor", max(0, int(arg or 0))
        except ValueError:
            pass
    return "monitor", 0


def find_window(text: str) -> Window | None:
    t = text.casefold()
    for w in windows():
        if w.title.casefold() == t:
            return w
    for w in windows():
        if t in w.title.casefold():
            return w
    return None


def describe(target: str | None) -> str:
    kind, arg = parse_target(target)
    return f"window “{arg}”" if kind == "window" else f"screen {int(arg) + 1}"


# ------------------------------------------------------------------------------------------- grabbers
class Grabber:
    """One backend bound to one target. read() -> BGRA array (a reused buffer: copy before keeping it) or None
    when nothing new arrived; raises CaptureError when the target is gone."""
    backend = ""

    def read(self, timeout_ms: int) -> np.ndarray | None:
        raise NotImplementedError

    def close(self) -> None:
        pass


_libs: dict[str, ctypes.CDLL | None] = {}


def _lib(name: str) -> ctypes.CDLL | None:
    if name not in _libs:
        from vcstudio.core.resources import native_lib
        p = native_lib(f"{name}.dll")
        try:
            _libs[name] = ctypes.CDLL(str(p)) if p.exists() else None
        except OSError as e:
            log.warning("%s: %s", p.name, e)
            _libs[name] = None
        lib = _libs[name]
        if lib is not None and name == "vcs_wgc":
            ip = ctypes.POINTER(ctypes.c_int)
            lib.wgc_supported.restype = ctypes.c_int
            lib.wgc_open_monitor.restype = ctypes.c_void_p
            lib.wgc_open_monitor.argtypes = [ctypes.c_int, ip, ip, ctypes.c_wchar_p, ctypes.c_int]
            lib.wgc_open_window.restype = ctypes.c_void_p
            lib.wgc_open_window.argtypes = [wintypes.HWND, ip, ip, ctypes.c_wchar_p, ctypes.c_int]
            lib.wgc_read.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ip, ip]
            lib.wgc_close.argtypes = [ctypes.c_void_p]
        elif lib is not None and name == "vcs_dupl":
            ip = ctypes.POINTER(ctypes.c_int)
            lib.dupl_monitors.restype = ctypes.c_int
            lib.dupl_open.restype = ctypes.c_void_p
            lib.dupl_open.argtypes = [ctypes.c_int, ip, ip, ctypes.c_wchar_p, ctypes.c_int, ctypes.c_wchar_p,
                                      ctypes.c_int]
            lib.dupl_read.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
            lib.dupl_close.argtypes = [ctypes.c_void_p]
    return _libs[name]


def wgc_supported() -> bool:
    lib = _lib("vcs_wgc")
    return bool(lib is not None and lib.wgc_supported())


class WgcGrabber(Grabber):
    backend = "wgc"

    def __init__(self, kind: str, arg):
        lib = _lib("vcs_wgc")
        if lib is None or not lib.wgc_supported():
            raise CaptureError("Windows Graphics Capture is not available")
        self._lib = lib
        w, h, err = ctypes.c_int(), ctypes.c_int(), ctypes.create_unicode_buffer(256)
        self._h = (lib.wgc_open_window(arg, ctypes.byref(w), ctypes.byref(h), err, 256) if kind == "window"
                   else lib.wgc_open_monitor(int(arg), ctypes.byref(w), ctypes.byref(h), err, 256))
        if not self._h:
            raise CaptureError(err.value or "Windows Graphics Capture failed")
        self._w, self._h_px = ctypes.c_int(w.value), ctypes.c_int(h.value)
        self._buf = np.empty((h.value, w.value, 4), np.uint8)

    def read(self, timeout_ms: int) -> np.ndarray | None:
        if not self._h:
            raise CaptureError("closed")
        r = self._lib.wgc_read(self._h, self._buf.ctypes.data, self._buf.nbytes, timeout_ms,
                               ctypes.byref(self._w), ctypes.byref(self._h_px))
        if r == -3 or r == -2:                    # size changed: next frame comes at the new size
            self._buf = np.empty((max(1, self._h_px.value), max(1, self._w.value), 4), np.uint8)
            return None
        if r < 0:
            raise CaptureError("the captured window or screen is gone")
        return self._buf if r == 1 else None

    def close(self) -> None:
        if self._h:
            self._lib.wgc_close(self._h)
            self._h = None


class DuplGrabber(Grabber):
    backend = "dupl"

    def __init__(self, index: int):
        lib = _lib("vcs_dupl")
        if lib is None:
            raise CaptureError("Desktop Duplication helper missing")
        self._lib, self._index = lib, index
        w, h = ctypes.c_int(), ctypes.c_int()
        name, err = ctypes.create_unicode_buffer(64), ctypes.create_unicode_buffer(256)
        self._h = lib.dupl_open(index, ctypes.byref(w), ctypes.byref(h), name, 64, err, 256)
        if not self._h:
            raise CaptureError(err.value or "Desktop Duplication failed")
        self._buf = np.empty((h.value, w.value, 4), np.uint8)

    def read(self, timeout_ms: int) -> np.ndarray | None:
        if not self._h:
            raise CaptureError("closed")
        r = self._lib.dupl_read(self._h, self._buf.ctypes.data, self._buf.nbytes, timeout_ms)
        if r < 0:     # mode change, secure desktop, session switch: reopen (the supervisor reconnects)
            raise CaptureError("screen duplication lost (display change or secure desktop)")
        return self._buf if r == 1 else None

    def close(self) -> None:
        if self._h:
            self._lib.dupl_close(self._h)
            self._h = None


class GdiGrabber(Grabber):
    """BitBlt of a monitor rectangle, or PrintWindow(PW_RENDERFULLCONTENT) of one window. Polls at the caller's
    rate; every read is a full new picture."""
    backend = "gdi"

    def __init__(self, kind: str, arg):
        self.kind = kind
        if kind == "window":
            self.hwnd = int(arg)
            if not _u32.IsWindow(self.hwnd):
                raise CaptureError("the window is gone")
        else:
            mons = monitors()
            if int(arg) >= len(mons):
                raise CaptureError(f"no screen {int(arg) + 1}")
            self.mon = mons[int(arg)]
        self._last = 0.0

    def _rect(self) -> tuple[int, int, int, int]:
        if self.kind == "window":
            if not _u32.IsWindow(self.hwnd):
                raise CaptureError("the captured window was closed")
            r = wintypes.RECT()
            _u32.GetWindowRect(self.hwnd, ctypes.byref(r))
            return 0, 0, max(1, r.right - r.left), max(1, r.bottom - r.top)
        m = self.mon
        return m.left, m.top, m.width, m.height

    def read(self, timeout_ms: int) -> np.ndarray | None:
        x, y, w, h = self._rect()
        if self.kind == "window" and _u32.IsIconic(self.hwnd):
            time.sleep(timeout_ms / 1000)
            return None                         # minimised: nothing to draw
        src = _u32.GetWindowDC(self.hwnd) if self.kind == "window" else _u32.GetDC(None)
        if not src:
            raise CaptureError("GetDC failed (no interactive desktop?)")
        mem = _g32.CreateCompatibleDC(src)
        bmp = _g32.CreateCompatibleBitmap(src, w, h)
        old = _g32.SelectObject(mem, bmp)
        try:
            ok = (_u32.PrintWindow(self.hwnd, mem, PW_RENDERFULLCONTENT) if self.kind == "window"
                  else _g32.BitBlt(mem, 0, 0, w, h, src, x, y, SRCCOPY | CAPTUREBLT))
            if not ok:
                return None
            bih = _BIH(biSize=ctypes.sizeof(_BIH), biWidth=w, biHeight=-h, biPlanes=1, biBitCount=32)
            buf = np.empty((h, w, 4), np.uint8)
            if _g32.GetDIBits(mem, bmp, 0, h, buf.ctypes.data, ctypes.byref(bih), 0) != h:
                return None
            buf[..., 3] = 255
            return buf
        finally:
            _g32.SelectObject(mem, old)
            _g32.DeleteObject(bmp)
            _g32.DeleteDC(mem)
            _u32.ReleaseDC(self.hwnd if self.kind == "window" else None, src)


def _make(backend: str, kind: str, arg) -> Grabber:
    if backend == "wgc":
        return WgcGrabber(kind, arg)
    if backend == "dupl":
        if kind == "window":
            raise CaptureError("Desktop Duplication captures whole screens only")
        return DuplGrabber(int(arg))
    return GdiGrabber(kind, arg)


def open_grabber(target: str | None, backends: tuple[str, ...] = BACKENDS,
                 first_frame_s: float = FIRST_FRAME_S) -> tuple[Grabber, np.ndarray, list[str]]:
    """First backend that really delivers a picture of `target`: (grabber, first frame, notes on the ones skipped)."""
    kind, arg = parse_target(target)
    if kind == "window":
        win = find_window(str(arg))
        if win is None:
            raise CaptureError(f"no open window titled “{arg}”")
        arg = win.hwnd
    notes: list[str] = []
    for b in backends:
        try:
            g = _make(b, kind, arg)
        except CaptureError as e:
            notes.append(f"{b}: {e}")
            continue
        end = time.monotonic() + first_frame_s
        try:
            while time.monotonic() < end:
                img = g.read(100)
                if img is not None:
                    return g, img.copy(), notes
        except CaptureError as e:
            notes.append(f"{b}: {e}")
            g.close()
            continue
        g.close()
        notes.append(f"{b}: no picture within {first_frame_s:g} s")
    raise CaptureError("screen capture failed (" + "; ".join(notes) + ")")


def grab(target: str | None = DEFAULT_TARGET, backends: tuple[str, ...] = BACKENDS) -> tuple[np.ndarray, str]:
    """One picture of a screen or window: (BGR array, backend used)."""
    g, img, _ = open_grabber(target, backends)
    try:
        return np.ascontiguousarray(img[..., :3]), g.backend
    finally:
        g.close()


# ------------------------------------------------------------------------------------------- input source
class ScreenCaptureSource(CaptureSource):
    """A screen or window as the video input. Frames are delivered at `fps`; between screen changes the last
    picture is repeated (a still screen is a valid picture, not a lost signal)."""
    kind = "screen"

    def __init__(self, target: str | None = DEFAULT_TARGET, fps: float = 30.0,
                 backends: tuple[str, ...] = BACKENDS):
        self.target = target or DEFAULT_TARGET
        self.fps = max(1.0, min(float(fps), 60.0))
        self.backends = backends
        self.backend = ""
        self.name = f"Screen capture: {describe(self.target)}"
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._nominal = (0, 0, self.fps)

    @property
    def nominal(self):
        return self._nominal

    def is_present(self) -> bool:
        kind, arg = parse_target(self.target)
        return find_window(str(arg)) is not None if kind == "window" else int(arg) < len(monitors())

    def start(self, on_frame, on_event) -> None:
        g, first, notes = open_grabber(self.target, self.backends)
        self.backend = g.backend
        h, w = first.shape[:2]
        self._nominal = (w, h, self.fps)
        if notes:
            log.info("screen capture: skipped %s", "; ".join(notes))
        log.info("screen capture: %s, %dx%d @ %g fps via %s", describe(self.target), w, h, self.fps, g.backend)
        # The thread owns the grabber and closes it when it ends (closing it under a read would free the native
        # capture objects while they are in use).
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._owned, args=(g, first, self._stop, on_frame, on_event),
                                        name="screen-capture", daemon=True)
        self._thread.start()

    def _owned(self, g, first, stop, on_frame, on_event) -> None:
        try:
            self._run(g, first, stop, on_frame, on_event)
        except CaptureError as e:
            on_event("disconnected", str(e))
        except Exception as e:                       # noqa: BLE001 (report, the supervisor reconnects)
            log.exception("screen capture failed")
            on_event("disconnected", f"screen capture failed: {e}")
        finally:
            g.close()

    def _run(self, g, first, stop, on_frame, on_event) -> None:
        from vcstudio.platform.win.capture import FramePool
        pool = FramePool()
        period = 1.0 / self.fps
        latest = first
        seq = 0
        nxt = time.perf_counter()
        polled = g.backend == "gdi"                  # GDI copies a full picture on every call: once per period
        while not stop.is_set():
            wait = nxt - time.perf_counter()
            if polled:
                if wait > 0:
                    stop.wait(wait)
                    continue
                img = g.read(0)
            else:
                img = g.read(max(1, min(int(wait * 1000), 100)))
            if img is not None:
                latest = img
            if time.perf_counter() < nxt:
                continue
            nxt += period
            if nxt < time.perf_counter():
                nxt = time.perf_counter() + period
            h, w = latest.shape[:2]
            out = pool.get(h, w)
            np.copyto(out, latest)
            seq += 1
            now = host_time_s()
            if stop.is_set():
                return
            on_frame(Frame(seq, now, now, w, h, array=out, source=self.name))

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(2)
            if t.is_alive():
                log.warning("screen capture thread did not stop within 2 s; it closes the capture when it ends")
        self._thread = None
