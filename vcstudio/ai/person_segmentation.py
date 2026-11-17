"""Person (foreground) segmentation for background replacement and blur.

Model: PP-HumanSeg (PaddlePaddle, Apache-2.0), the OpenCV Zoo ONNX port human_segmentation_pphumanseg_2023mar.onnx:
input 1x3x192x192 RGB, (x / 255 - 0.5) / 0.5; output 1x2x192x192 class scores (0 background, 1 person).
The person probability is upscaled to the frame and smoothed over time, so the edge does not flicker.

BackgroundSegmenter runs on its own thread at a limited rate and keeps only the newest mask (LatestSlot): the camera
output never waits for it; the pipeline blends the newest mask into every frame.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from vcstudio.core.frame_queue import LatestSlot

log = logging.getLogger(__name__)

MODEL_FILE = "human_segmentation_pphumanseg_2023mar.onnx"
SIZE = 192


def model_path() -> Path:
    from vcstudio.core.resources import models_dir
    return models_dir() / MODEL_FILE


class PersonSegmenter:
    """One inference: BGRA frame -> person probability (h, w) float32 0..1 at `out_size` (default: model size)."""

    def __init__(self, path: Path | None = None, compute_units: str = "all", cache_dir: Path | None = None):
        from vcstudio.ai.onnx_engine import create_session
        p = path or model_path()
        if not p.exists():
            raise FileNotFoundError(f"person segmentation model missing: {p} (scripts/fetch_models.py)")
        self.sess, self.backend = create_session(p, compute_units, cache_dir)
        self.input = self.sess.get_inputs()[0].name

    def probability(self, bgra: np.ndarray) -> np.ndarray:
        small = cv2.resize(bgra[..., :3], (SIZE, SIZE), interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB).astype(np.float32)
        x = ((rgb / 255.0 - 0.5) / 0.5).transpose(2, 0, 1)[None]
        scores = self.sess.run(None, {self.input: np.ascontiguousarray(x)})[0][0]      # (2, 192, 192)
        e = np.exp(scores - scores.max(0, keepdims=True))
        return (e[1] / e.sum(0)).astype(np.float32)                                    # softmax, person class


class BackgroundSegmenter:
    """Background thread: newest captured frame -> smoothed person mask (192x192 float), published in `masks`."""

    def __init__(self, capture, fps: float = 15.0, smoothing: float = 0.5, segmenter_factory=None):
        self.capture = capture
        self.fps = fps
        self.smoothing = smoothing                 # weight of the previous mask (0 = no smoothing)
        self.masks: LatestSlot[np.ndarray] = LatestSlot()
        self._factory = segmenter_factory or PersonSegmenter
        self._seg: PersonSegmenter | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.error = ""
        self.backend = ""
        self.ms = 0.0
        self.runs = 0
        self.rate_scale = 1.0                      # set by the resource scheduler (vcstudio/core/scheduler.py)
        from vcstudio.core.performance import FpsCounter
        self._rate = FpsCounter(2.0)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def rate_fps(self) -> float:
        return self._rate.rate()

    def start(self) -> None:
        if self.running:
            return
        self._stop = threading.Event()             # own event per worker (see AIEngine.start)
        self._thread = threading.Thread(target=self._run, args=(self._stop,), name="background-seg", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None:
            t.join(2)
        self._thread = None
        self.masks.clear()

    def _run(self, stop: threading.Event) -> None:
        last_ver, prev = -1, None
        try:
            self._seg = self._seg or self._factory()
            self.backend, self.error = getattr(self._seg, "backend", ""), ""
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            log.error("Background segmentation unavailable: %s", self.error)
            return
        while not stop.is_set():
            period = 1.0 / max(1.0, self.fps * self.rate_scale)   # re-read: the scheduler changes it at any time
            t0 = time.perf_counter()
            frame, ver = self.capture.slot.wait_newer(last_ver, timeout=0.25)
            if frame is None or ver == last_ver:
                continue
            last_ver = ver
            try:
                with frame.bgra() as img:
                    p = self._seg.probability(img)
            except Exception as e:                 # never stops the camera: the pipeline just has no new mask
                self.error = f"{type(e).__name__}: {e}"
                log.warning("background segmentation failed: %s", self.error)
                stop.wait(0.5)
                continue
            prev = p if prev is None or prev.shape != p.shape else self.smoothing * prev + (1 - self.smoothing) * p
            if stop.is_set():
                break
            self.masks.put(prev)
            self.runs += 1
            self._rate.tick()
            self.ms = (time.perf_counter() - t0) * 1000
            rest = period - (time.perf_counter() - t0)
            if rest > 0:
                stop.wait(rest)


def composite_background(frame_bgra: np.ndarray, mask192: np.ndarray, mode: str, strength: int = 25,
                         image_bgra: np.ndarray | None = None, edge: float = 0.5,
                         out: np.ndarray | None = None) -> np.ndarray:
    """Blend: person from the frame, background blurred (mode "blur") or replaced (mode "image").

    The mask is upscaled with a soft edge: values are remapped around `edge` (0..1) so the outline is crisp but not
    aliased. Blur runs on a quarter-size copy (much cheaper than a full-size Gaussian, visually the same for a
    background). Returns a new BGRA array (or `out`)."""
    h, w = frame_bgra.shape[:2]
    if mode not in ("blur", "image") or (mode == "image" and image_bgra is None):
        return frame_bgra
    # edge remap and 1 - m on the small mask, then both weights scaled up: the same order as the GPU layer (mask
    # remapped, then sampled bilinearly); remapping 2 M full-size values cost ~11 of the ~24 ms per 1080p frame
    lo, hi = max(0.0, edge - 0.25), min(1.0, edge + 0.25)
    small_m = np.clip((np.asarray(mask192, np.float32) - lo) / max(1e-6, hi - lo), 0.0, 1.0)
    m = cv2.resize(small_m, (w, h), interpolation=cv2.INTER_LINEAR)
    inv = cv2.resize(1.0 - small_m, (w, h), interpolation=cv2.INTER_LINEAR)
    if mode == "blur":
        k = max(1, int(strength)) | 1
        small = cv2.resize(frame_bgra, (max(1, w // 4), max(1, h // 4)), interpolation=cv2.INTER_AREA)
        small = cv2.GaussianBlur(small, (0, 0), sigmaX=max(0.5, k / 4))
        bg = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
    else:
        assert image_bgra is not None
        bg = image_bgra if image_bgra.shape[:2] == (h, w) else cv2.resize(image_bgra, (w, h), interpolation=cv2.INTER_AREA)
    res = cv2.blendLinear(np.ascontiguousarray(frame_bgra), np.ascontiguousarray(bg), m, inv)   # SIMD, uint8
    if out is None:
        return res
    np.copyto(out, res)
    return out
