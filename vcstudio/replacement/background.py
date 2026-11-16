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

    def apply(self, frame: Frame) -> Frame:
        """The frame with its background blurred / replaced, or the frame itself when there is nothing to do."""
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
