"""Monocular depth (0.6.0): Depth Anything V2 Small, 8-bit ONNX (Apache-2.0; downloaded on the user's click with
SHA-256 check, vcstudio/ai/model_download.py DEPTH).

DepthSegmenter has the same interface as the person segmenter (`probability(bgra) -> float map 0..1`), so the
background service (vcstudio/replacement/background.py, source "depth") uses it unchanged: the map is "nearness"
(1 = closest), normalised per frame between the 2nd and 98th percentile, and the background's edge setting becomes the
focus distance — what is nearer stays sharp, what is farther is blurred or replaced.

Input: RGB, ImageNet mean / std, sides multiples of 14. Measured on the 2-core server (CPU): 266×154 ≈ 0.4 s,
392×224 ≈ 0.95 s, 518×518 ≈ 2.5 s per frame — so the default input is 266 px wide and depth runs at ~2 maps / s
there, smoothed over time by the segmentation thread. The output is relative (no metres).
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

DEPTH_FILE = "depth/depth_anything_v2_small_int8.onnx"
MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)


def models_dir() -> Path:
    from vcstudio.ai.open_vocab import user_models_dir
    from vcstudio.core.resources import models_dir as md
    bundled = md()
    return bundled if (bundled / DEPTH_FILE).exists() else user_models_dir()


def available() -> bool:
    return (models_dir() / DEPTH_FILE).exists()


def input_size(w: int, h: int, width: int) -> tuple[int, int]:
    """Model input (W, H): `width` wide, aspect kept, both multiples of 14 (pure: tested)."""
    iw = max(14, int(round(width / 14)) * 14)
    ih = max(14, int(round(width * h / max(1, w) / 14)) * 14)
    return iw, ih


class DepthSegmenter:
    def __init__(self, width: int = 266, cache_dir: Path | None = None):
        from vcstudio.ai.onnx_engine import create_session
        self.sess, self.backend = create_session(models_dir() / DEPTH_FILE, "all", cache_dir)
        self.inp = self.sess.get_inputs()[0].name
        self.width = width

    def depth(self, bgra: np.ndarray) -> np.ndarray:
        """Raw relative inverse depth (bigger = nearer) at the model's resolution."""
        h, w = bgra.shape[:2]
        iw, ih = input_size(w, h, self.width)
        x = cv2.cvtColor(cv2.resize(bgra, (iw, ih), interpolation=cv2.INTER_AREA), cv2.COLOR_BGRA2RGB)
        x = ((x.astype(np.float32) / 255.0 - MEAN) / STD).transpose(2, 0, 1)[None]
        return self.sess.run(None, {self.inp: np.ascontiguousarray(x, np.float32)})[0][0]

    def probability(self, bgra: np.ndarray) -> np.ndarray:
        d = self.depth(bgra)
        lo, hi = np.percentile(d, (2, 98))
        return np.clip((d - lo) / max(1e-6, hi - lo), 0.0, 1.0).astype(np.float32)
