"""Benchmark input: cycles pre-rendered IOSurface pixel buffers, like AVFoundation capture (zero-copy).

The synthetic TestPatternSource builds a NumPy frame every tick (8 MB at 1080p on the Python GIL),
which distorts CPU/latency measurements. BenchSource renders N variants once (a scene plus a
frame-index bar code) and then only hands out pixel-buffer references at the requested rate, so
the measured cost is the pipeline's, not the source's.
"""
from __future__ import annotations

import time

import numpy as np

from vcstudio.core.frame import Frame, host_time_s
from vcstudio.core.video_capture import TestPatternSource

BITS = 10


class BenchSource(TestPatternSource):
    __test__ = False

    def __init__(self, scene: np.ndarray, fps: float = 60.0, variants: int = 8):
        h, w = scene.shape[:2]
        super().__init__(w, h, fps)
        self.name = "Benchmark scene"
        from vcstudio.core.pixelbuffer import PixelBufferPool
        self._pool = PixelBufferPool(w, h)
        cell = w // BITS
        self._pbs = []
        for v in range(variants):
            img = np.ascontiguousarray(scene).copy()
            for b in range(BITS):
                img[h - 40:, b * cell:(b + 1) * cell, :3] = 255 if (v >> b) & 1 else 0
            self._pbs.append(self._pool.from_array(img))

    def _run(self, on_frame) -> None:
        n, t0 = 0, time.perf_counter()
        while not self._stop.is_set():
            if not self.stalled.is_set():
                n += 1
                now = host_time_s()
                on_frame(Frame(n, now, now, self.width, self.height,
                               pixel_buffer=self._pbs[0 if self.frozen.is_set() else n % len(self._pbs)],
                               source=self.name))
            d = t0 + (n + 1) / self.fps - time.perf_counter()
            if d > 0:
                self._stop.wait(d)
            else:
                t0, n = time.perf_counter(), 0


def sample_scene(width: int = 1920, height: int = 1080) -> np.ndarray:
    """The sample photo (people, buses) scaled to the frame height and centred on a grey background, BGRA."""
    import cv2
    from vcstudio.ai.testimages import load_bgra
    from vcstudio.core.resources import sample_photo
    photo = load_bgra(sample_photo())
    ph, pw = photo.shape[:2]
    scene = np.zeros((height, width, 4), np.uint8)
    scene[..., 3] = 255
    scene[..., :3] = (60, 70, 80)
    bw = min(width, int(round(pw * height / ph)))
    bh = int(round(ph * bw / pw))
    x0, y0 = (width - bw) // 2, (height - bh) // 2
    scene[y0:y0 + bh, x0:x0 + bw] = cv2.resize(photo, (bw, bh), interpolation=cv2.INTER_AREA)
    return scene


bus_scene = sample_scene          # old name (scripts, tests)
