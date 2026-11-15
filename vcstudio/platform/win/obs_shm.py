"""Direct frame transfer from OBS on Windows: reader for the vcstudio-obs plugin's shared memory.

Same protocol as macOS (native/obs-plugin/vcs_frame_shm.h): 4 KiB header + 3 slots, per-slot seqlock,
the writer never waits. On Windows the section "Local\\vcs-obs-frames" is reserved for the maximum size
and the plugin commits the header plus width*height*4 bytes of each slot, so the reader touches exactly
that and nothing more (reading reserved-but-uncommitted pages would fault).

No native helper library is needed: the view is mapped with ctypes and the pixels are copied with
NumPy (which releases the GIL for large copies) straight into the frame's BGRA buffer.
"""
from __future__ import annotations

import ctypes
import logging
import threading
import time
from ctypes import wintypes

import numpy as np

from vcstudio.core.frame import Frame, host_time_s
from vcstudio.core.video_capture import CaptureError, CaptureSource

log = logging.getLogger(__name__)

SHM_NAME = "Local\\vcs-obs-frames"
MAGIC, VERSION, SLOTS, HEADER_BYTES = 0x46534356, 1, 3, 4096
MAX_W, MAX_H = 3840, 2160
STALE_S = 1.0
FILE_MAP_READ = 0x0004

# header field offsets (vcs_shm_header, natural alignment on x64)
O_MAGIC, O_VERSION, O_SLOTS, O_HEADER, O_SLOT_BYTES = 0, 4, 8, 12, 16
O_LATEST, O_HEARTBEAT, O_PID, O_FPS_NUM, O_FPS_DEN, O_ACTIVE, O_SLOT0 = 24, 32, 40, 44, 48, 52, 56
SLOT_SIZE = 32          # seq u64, width u32, height u32, stride u32, format u32, timestamp_ns u64

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
_k32.OpenFileMappingW.restype = wintypes.HANDLE
_k32.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
_k32.MapViewOfFile.restype = ctypes.c_void_p
_k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]


def now_ns() -> int:
    """os_gettime_ns() on Windows is QueryPerformanceCounter in ns, as is perf_counter_ns."""
    return time.perf_counter_ns()


class ShmView:
    """Read-only view of the plugin's shared memory."""

    def __init__(self, name: str = SHM_NAME):
        self.handle = _k32.OpenFileMappingW(FILE_MAP_READ, False, name)
        if not self.handle:
            raise FileNotFoundError(name)
        self.base = _k32.MapViewOfFile(self.handle, FILE_MAP_READ, 0, 0, 0)
        if not self.base:
            err = ctypes.get_last_error()
            _k32.CloseHandle(self.handle)
            raise OSError(f"MapViewOfFile failed ({err})")

    def u32(self, off: int) -> int:
        return ctypes.c_uint32.from_address(self.base + off).value

    def u64(self, off: int) -> int:
        return ctypes.c_uint64.from_address(self.base + off).value

    def i32(self, off: int) -> int:
        return ctypes.c_int32.from_address(self.base + off).value

    def valid(self) -> str:
        if self.u32(O_MAGIC) != MAGIC:
            return "shared memory has an unexpected size"           # same wording as the macOS reader
        if self.u32(O_VERSION) != VERSION:
            return "incompatible plugin protocol version"
        return ""

    def slot(self, i: int) -> tuple[int, int, int, int, int, int]:
        o = O_SLOT0 + i * SLOT_SIZE
        return (self.u64(o), self.u32(o + 8), self.u32(o + 12), self.u32(o + 16), self.u32(o + 20), self.u64(o + 24))

    def read_latest(self, dst_pool, last: int) -> tuple[int, np.ndarray, int] | None:
        """Newest complete frame newer than `last`: (frame number, BGRA array, timestamp_ns) or None."""
        slot_bytes = self.u64(O_SLOT_BYTES)
        for _ in range(4):                             # retry when the writer overtakes us mid-copy
            n = self.u64(O_LATEST)
            if n == 0 or n == last:
                return None
            i = n % SLOTS
            seq, w, h, stride, fmt, ts = self.slot(i)
            if seq != 2 * n or not (0 < w <= MAX_W and 0 < h <= MAX_H) or stride != w * 4 or fmt != 1:
                continue
            if h * stride > slot_bytes:
                return None
            src = np.ctypeslib.as_array((ctypes.c_uint8 * (h * stride)).from_address(
                self.base + HEADER_BYTES + i * slot_bytes)).reshape(h, w, 4)
            out = dst_pool.get(h, w)
            np.copyto(out, src)
            if self.u64(O_SLOT0 + i * SLOT_SIZE) == seq:   # unchanged: the copy is consistent
                return n, out, ts
        return None

    def close(self) -> None:
        if self.base:
            _k32.UnmapViewOfFile(self.base)
            self.base = None
        if self.handle:
            _k32.CloseHandle(self.handle)
            self.handle = None


def obs_sharing_status(name: str = SHM_NAME) -> dict:
    """{'available': bool, 'reason': str, ...frame info} without starting capture."""
    try:
        v = ShmView(name)
    except FileNotFoundError:
        return {"available": False, "reason": "OBS is not sharing frames (install the vcstudio-obs plugin and "
                                              "enable Tools > AI Virtual Camera Studio: share frames)"}
    except OSError as e:
        return {"available": False, "reason": str(e)}
    try:
        bad = v.valid()
        if bad:
            return {"available": False, "reason": bad}
        hb = v.u64(O_HEARTBEAT)
        age = (now_ns() - hb) / 1e9 if hb else None
        active = bool(v.u32(O_ACTIVE))
        latest = v.u64(O_LATEST)
        _, w, h, *_ = v.slot(latest % SLOTS) if latest else (0, 0, 0)
        fnum, fden = v.u32(O_FPS_NUM), v.u32(O_FPS_DEN)
        live = active and age is not None and age < STALE_S
        return {"available": live, "reason": "" if live else ("OBS stopped sharing" if not active else "no recent frames"),
                "width": w, "height": h, "fps": (fnum / fden) if fden else 0, "writer_pid": v.i32(O_PID),
                "frames": latest, "age_s": age}
    finally:
        v.close()


class ObsShmSource(CaptureSource):
    kind = "obs_ipc"

    def __init__(self, name: str = SHM_NAME):
        self.shm_name = name
        self.name = "OBS (direct frame transfer)"
        self._view: ShmView | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._nominal = (0, 0, 0.0)

    @property
    def nominal(self):
        return self._nominal

    def is_present(self) -> bool:
        return obs_sharing_status(self.shm_name)["available"]

    def start(self, on_frame, on_event) -> None:
        status = obs_sharing_status(self.shm_name)
        if not status["available"]:
            raise CaptureError(status["reason"])
        try:
            self._view = ShmView(self.shm_name)
        except OSError as e:
            raise CaptureError(f"cannot open OBS shared memory ({e})") from e
        self._nominal = (status["width"], status["height"], status["fps"])
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(on_frame, on_event), name="obs-shm-reader", daemon=True)
        self._thread.start()
        log.info("OBS direct frame transfer: %dx%d @ %.2f fps (OBS pid %s)", status["width"], status["height"],
                 status["fps"], status["writer_pid"])

    def _run(self, on_frame, on_event) -> None:
        from vcstudio.platform.win.capture import FramePool
        v = self._view
        pool = FramePool()
        last, seq = 0, 0
        while not self._stop.is_set() and v is not None:
            if not v.u32(O_ACTIVE):
                on_event("disconnected", "OBS stopped sharing frames")
                self._stop.wait(0.2)
                continue
            got = v.read_latest(pool, last)
            if got is None:
                time.sleep(0.001)            # high-resolution sleep on Python 3.11+ (waitable timer)
                continue
            n, img, ts_ns = got
            last = n
            seq += 1
            now = host_time_s()
            pts = ts_ns / 1e9
            h, w = img.shape[:2]
            on_frame(Frame(seq, pts if 0 <= now - pts < 1.0 else now, now, w, h, array=img, source=self.name))

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(2)
        self._thread = None
        if self._view:
            self._view.close()
            self._view = None
