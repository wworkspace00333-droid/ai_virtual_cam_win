"""CVPixelBuffer helpers: wrap NumPy BGRA frames in IOSurface-backed pixel buffers (pooled)."""
from __future__ import annotations

import threading

import numpy as np

from vcstudio.platform import IS_MAC


class PixelBufferPool:
    """Small CVPixelBufferPool for a fixed size; avoids per-frame allocations.
    Windows: buffers are plain BGRA arrays; from_array returns a contiguous private copy."""

    def __init__(self, width: int, height: int):
        self.width, self.height = width, height
        self._pool = _cv_pool(width, height) if IS_MAC else None

    def from_array(self, arr: np.ndarray):
        h, w = arr.shape[:2]
        if (w, h) != (self.width, self.height) or arr.shape[2] != 4 or arr.dtype != np.uint8:
            raise ValueError("array does not match pool format")
        if not IS_MAC:
            return np.array(arr, order="C", copy=True)
        return _cv_fill(self._pool, arr)


def _cv_pool(width: int, height: int):  # pragma: macos
    import Quartz as Q  # type: ignore
    attrs = {
        Q.kCVPixelBufferPixelFormatTypeKey: Q.kCVPixelFormatType_32BGRA,
        Q.kCVPixelBufferWidthKey: width,
        Q.kCVPixelBufferHeightKey: height,
        Q.kCVPixelBufferIOSurfacePropertiesKey: {},
        Q.kCVPixelBufferMetalCompatibilityKey: True,
    }
    err, pool = Q.CVPixelBufferPoolCreate(None, {Q.kCVPixelBufferPoolMinimumBufferCountKey: 3}, attrs, None)
    if err != 0:
        raise RuntimeError(f"CVPixelBufferPoolCreate failed: {err}")
    return pool


def _cv_fill(pool, arr: np.ndarray):  # pragma: macos
    import Quartz as Q  # type: ignore
    h, w = arr.shape[:2]
    err, pb = Q.CVPixelBufferPoolCreatePixelBuffer(None, pool, None)
    if err != 0:
        raise RuntimeError(f"CVPixelBufferPoolCreatePixelBuffer failed: {err}")
    Q.CVPixelBufferLockBaseAddress(pb, 0)
    try:
        bpr = Q.CVPixelBufferGetBytesPerRow(pb)
        buf = Q.CVPixelBufferGetBaseAddress(pb).as_buffer(bpr * h)
        dst = np.frombuffer(buf, dtype=np.uint8).reshape(h, bpr)[:, : w * 4].reshape(h, w, 4)
        dst[...] = arr
    finally:
        Q.CVPixelBufferUnlockBaseAddress(pb, 0)
    return pb


_pools: dict[tuple[int, int], PixelBufferPool] = {}
_lock = threading.Lock()


def pixel_buffer_for(frame):
    """Native output buffer for a Frame.

    macOS: a CVPixelBuffer (zero-copy if the frame already has one; pooled copy for arrays).
    Windows: the frame's own contiguous BGRA array (no copy); the virtual camera and the D3D11
    compositor take arrays directly."""
    if frame.pixel_buffer is not None:
        return frame.pixel_buffer
    if frame.array is None:
        raise ValueError("frame has no pixel data")
    if not IS_MAC:
        a = frame.array
        return a if a.flags.c_contiguous else np.ascontiguousarray(a)
    return _pooled(frame)


def _pooled(frame):  # pragma: macos
    key = (frame.width, frame.height)
    with _lock:
        pool = _pools.get(key)
        if pool is None:
            if len(_pools) > 4:
                _pools.clear()
            pool = _pools[key] = PixelBufferPool(*key)
    return pool.from_array(frame.array)
