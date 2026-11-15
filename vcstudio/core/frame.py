"""Frame model shared by all pipeline stages.

A frame either wraps a native pixel buffer or a NumPy array (test pattern, decoded assets).
  macOS:   `pixel_buffer` is a CVPixelBuffer (zero-copy from AVFoundation/IOSurface).
  Windows: frames are contiguous BGRA NumPy arrays; `pixel_buffer` (when set) is that same array.
Pixel access goes through `bgra()`, which locks the buffer for the duration of the `with` block.
The returned view MUST NOT escape the block.
"""
from __future__ import annotations

import contextlib
import time
import zlib
from dataclasses import dataclass, field
from typing import Any, Iterator

import numpy as np

from vcstudio.platform import IS_MAC


def _make_host_clock():
    """Host clock in seconds, shared by capture, AI, pipeline and the virtual camera.

    macOS: CoreMedia's host time clock (mach_absolute_time), the clock AVFoundation stamps frames with.
    Windows: QueryPerformanceCounter (time.perf_counter), the clock Media Foundation and OBS
    (os_gettime_ns) use, so OBS timestamps and ours are directly comparable."""
    if IS_MAC:
        try:
            import CoreMedia as CM  # type: ignore
            clock = CM.CMClockGetHostTimeClock()
            return lambda: float(CM.CMTimeGetSeconds(CM.CMClockGetTime(clock)))
        except Exception:  # pragma: no cover
            return time.monotonic
    return time.perf_counter


_host_clock = _make_host_clock()


def host_time_s() -> float:
    """Seconds on the host clock (see _make_host_clock)."""
    return _host_clock()


@dataclass
class Frame:
    seq: int
    pts: float                  # presentation time, host-clock seconds
    arrival: float              # host-clock seconds when the app received it
    width: int
    height: int
    pixel_format: str = "BGRA"
    pixel_buffer: Any = None    # CVPixelBufferRef (PyObjC keeps it retained)
    array: np.ndarray | None = None
    source: str = ""
    meta: dict = field(default_factory=dict)

    @contextlib.contextmanager
    def bgra(self) -> Iterator[np.ndarray]:
        """Read-only (H, W, 4) uint8 BGRA view."""
        if self.array is not None:
            yield self.array
            return
        if self.pixel_buffer is None:
            raise ValueError("frame has no pixel data")
        if isinstance(self.pixel_buffer, np.ndarray):      # Windows: the "pixel buffer" is the array
            yield self.pixel_buffer
            return
        import Quartz as Q  # type: ignore
        pb = self.pixel_buffer
        if Q.CVPixelBufferLockBaseAddress(pb, Q.kCVPixelBufferLock_ReadOnly) != 0:
            raise RuntimeError("CVPixelBufferLockBaseAddress failed")
        try:
            bpr = Q.CVPixelBufferGetBytesPerRow(pb)
            h = Q.CVPixelBufferGetHeight(pb)
            w = Q.CVPixelBufferGetWidth(pb)
            buf = Q.CVPixelBufferGetBaseAddress(pb).as_buffer(bpr * h)
            arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, bpr)[:, : w * 4].reshape(h, w, 4)
            arr.flags.writeable = False
            yield arr
        finally:
            Q.CVPixelBufferUnlockBaseAddress(pb, Q.kCVPixelBufferLock_ReadOnly)

    def signature(self, step: int = 24) -> int:
        """Cheap content fingerprint (subsampled CRC32) used for frozen-frame detection."""
        with self.bgra() as a:
            return zlib.crc32(np.ascontiguousarray(a[::step, ::step, :3]).data)

    def downscaled_copy(self, max_width: int) -> np.ndarray:
        """Contiguous BGRA copy with integer-step decimation so width <= max_width (preview use)."""
        with self.bgra() as a:
            step = max(1, -(-a.shape[1] // max(1, max_width)))
            return np.ascontiguousarray(a[::step, ::step])

    @property
    def age_ms(self) -> float:
        return (host_time_s() - self.arrival) * 1000.0
