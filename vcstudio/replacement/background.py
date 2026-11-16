"""Background replacement and blur behind people, for the whole frame (profile section "background").

Modes: off | blur (strength = blur size) | image (an image asset from the profile's asset library). The person mask
comes from vcstudio.ai.person_segmentation.BackgroundSegmenter (own thread, newest mask only); the output thread
blends the newest mask into each frame before the region replacements are drawn. Without a mask yet (model loading,
first frames) or after a segmentation error, frames pass unchanged: the camera never waits and never goes black.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

import numpy as np

from vcstudio.core.frame import Frame

log = logging.getLogger(__name__)

MODES = ("off", "blur", "image")


@dataclass
class BackgroundConfig:
    mode: str = "off"
    strength: int = 25
    asset_id: str | None = None
    edge: float = 0.5
    fps: float = 15.0

    @classmethod
    def from_profile(cls, body: dict) -> "BackgroundConfig":
        b = body.get("background") or {}
        mode = b.get("mode", "off")
        return cls(mode=mode if mode in MODES else "off", strength=int(min(99, max(1, b.get("strength", 25)))),
                   asset_id=b.get("asset_id"), edge=float(min(0.9, max(0.1, b.get("edge", 0.5)))),
                   fps=float(min(30, max(2, b.get("fps", 15)))))


class BackgroundService:
    def __init__(self, capture, assets, segmenter_factory=None):
        from vcstudio.ai.person_segmentation import BackgroundSegmenter
        self.assets = assets
        self.cfg = BackgroundConfig()
        self.seg = BackgroundSegmenter(capture, segmenter_factory=segmenter_factory)
        self._image: np.ndarray | None = None
        self._lock = threading.Lock()
        self.applied = 0
        self.error = ""

    @property
    def active(self) -> bool:
        return self.cfg.mode != "off" and (self.cfg.mode != "image" or self._image is not None)

    def load_profile(self, body: dict) -> None:
        cfg = BackgroundConfig.from_profile(body)
        image = None
        if cfg.mode == "image" and cfg.asset_id:
            from vcstudio.replacement.assets import assets_from_profile
            a = assets_from_profile(body).get(cfg.asset_id)
            try:
                image = self.assets.image(a) if a is not None else None
            except Exception as e:
                self.error = f"background image: {e}"
                log.warning(self.error)
        with self._lock:
            self.cfg, self._image = cfg, image
        self.seg.fps = cfg.fps
        if not self.active:
            self.seg.masks.clear()

    def start(self) -> None:
        if self.active:
            self.seg.start()

    def stop(self) -> None:
        self.seg.stop()

    def sync(self) -> None:
        """Start / stop the segmentation thread to match the configuration (capture running)."""
        if self.active and not self.seg.running:
            self.seg.start()
        elif not self.active and self.seg.running:
            self.seg.stop()

    # Measured on the 2-core development server (1080p, blur): compositor layers on software Direct3D (WARP) 34 ms,
    # on the CPU compositor 243 ms, the direct OpenCV blend (apply) 18-24 ms. So the layers are used only with a
    # hardware GPU compositor; otherwise apply() is faster. Hardware GPU timings: not measured yet.
    GPU_BACKENDS = ("d3d11", "metal")

    def use_gpu_layers(self, compositor_backend: str) -> bool:
        return compositor_backend in self.GPU_BACKENDS

    def apply(self, frame: Frame) -> Frame:
        """CPU path: the frame with its background blurred / replaced (OpenCV blend), or the frame itself."""
        if not self.active:
            return frame
        mask, _ = self.seg.masks.peek()
        if mask is None:
            return frame
        from vcstudio.ai.person_segmentation import composite_background
        with self._lock:
            cfg, image = self.cfg, self._image
        with frame.bgra() as img:
            out = composite_background(img, mask, cfg.mode, cfg.strength, image, cfg.edge)
        self.applied += 1
        return Frame(frame.seq, frame.pts, frame.arrival, frame.width, frame.height, array=out,
                     source=f"{frame.source}+background")

    def ops(self, frame: Frame) -> list:
        """Compositor layers that draw the background (empty: nothing to do / no mask yet).

        One "composite" layer over the whole frame: the source is a blurred quarter-size copy of the frame (blur) or
        the chosen image (image), scaled to the frame by the sampler; the layer's mask is "not a person". The GPU
        backend (Direct3D 11 / Metal) and the CPU backend run the same layer code (parity-tested), so the costly
        full-size blend runs on the GPU when there is one; the CPU only blurs a quarter-size image."""
        if not self.active:
            return []
        mask, ver = self.seg.masks.peek()
        if mask is None:
            return []
        import cv2

        from vcstudio.replacement.compositor import DrawOp
        with self._lock:
            cfg, image = self.cfg, self._image
        W, H = frame.width, frame.height
        lo, hi = max(0.0, cfg.edge - 0.25), min(1.0, cfg.edge + 0.25)
        person = np.clip((mask - lo) / max(1e-6, hi - lo), 0.0, 1.0)
        not_person = np.ascontiguousarray(((1.0 - person) * 255 + 0.5).astype(np.uint8))
        if cfg.mode == "blur":
            with frame.bgra() as img:
                small = cv2.resize(img, (max(1, W // 4), max(1, H // 4)), interpolation=cv2.INTER_AREA)
            src = cv2.GaussianBlur(small, (0, 0), sigmaX=max(0.5, (cfg.strength | 1) / 4))
            src[..., 3] = 255                                           # opaque (premultiplied = straight)
            src_key: tuple = ("bgblur", frame.seq)                      # changes every frame: uploaded per frame
        else:
            src = image
            src_key = ("img", "background", cfg.asset_id)               # uploaded once, kept on the GPU
        sh, sw = src.shape[:2]
        sx, sy = sw / W, sh / H
        inv = np.array([[sx, 0.0, 0.5 * sx - 0.5], [0.0, sy, 0.5 * sy - 0.5]])   # frame px -> src px (pixel centres)
        self.applied += 1
        return [DrawOp("__background__", "composite", (0, 0, W, H), 1.0, mask=not_person,
                       mask_rect=(0.0, 0.0, float(W), float(H)), mask_key=("bgmask", ver), src=np.ascontiguousarray(src),
                       src_key=src_key, inv=inv)]
