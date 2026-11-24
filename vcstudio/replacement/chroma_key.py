"""Chroma key (0.7.0): a green / blue / any-colour screen as the background mask (background source "chroma").

The keyer is a mask source for BackgroundSegmenter (vcstudio/ai/person_segmentation.py): its own thread, newest
mask only, the output never waits; so blur / image / colour / gradient / video backgrounds, mask quality (guided
edge refinement) and the GPU compositor layers all work with it unchanged.

Key: distance of each pixel's chroma (Cr, Cb of YCrCb, brightness ignored, so shadows on the screen still key) from
the key colour's chroma, in units of the key's own saturation (so the same `similarity` fits a pale and a vivid
screen): foreground alpha = clip((d - similarity) / smoothness, 0, 1). Pixels much darker or brighter than any
screen (Y below DARK_Y: hair, black clothes) are foreground whatever their tint. `feather` blurs the matte's edge.
Spill suppression (per output frame, before the blend): the key channel (green for a green screen) is pulled down
to the larger of the other two by `spill`, which removes the green rim and reflections on skin and hair.

Runs at KEY_SIDE pixels on the long side (the blend scales the matte up; Quality mode refines it against the
picture). Measured in tests/test_chroma_key.py on the development server (2 cores): 23.5 ms per matte from a 1080p
frame, on the keyer's own thread. Pure image processing.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

KEY_SIDE = 640
DARK_Y = 28
PRESETS = {"green": "#00b140", "blue": "#0047bb"}


def hex_bgr(h: str, default=(64, 177, 0)) -> tuple[int, int, int]:
    try:
        v = int(str(h).lstrip("#"), 16)
        return (v & 255, (v >> 8) & 255, (v >> 16) & 255)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class KeySettings:
    color: str = PRESETS["green"]
    similarity: float = 0.45          # 0..1: how close to the key colour still counts as screen
    smoothness: float = 0.15          # 0.01..1: width of the soft edge
    spill: float = 0.6                # 0..1
    feather: int = 1                  # matte blur radius (pixels at KEY_SIDE)


def _key_chroma(bgr: tuple[int, int, int]) -> tuple[np.ndarray, float]:
    px = np.array([[bgr]], np.uint8)
    ycc = cv2.cvtColor(px, cv2.COLOR_BGR2YCrCb)[0, 0].astype(np.float32)
    c = ycc[1:] - 128.0
    return c, max(8.0, float(np.linalg.norm(c)))


def foreground_alpha(bgr: np.ndarray, ks: KeySettings) -> np.ndarray:
    """(h, w) float32 0..1: 1 = keep (not the screen) (pure: tested)."""
    key, sat = _key_chroma(hex_bgr(ks.color))
    ycc = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb).astype(np.float32)
    d = np.sqrt((ycc[..., 1] - 128.0 - key[0]) ** 2 + (ycc[..., 2] - 128.0 - key[1]) ** 2) / sat
    a = np.clip((d - ks.similarity) / max(0.01, ks.smoothness), 0.0, 1.0)
    a[ycc[..., 0] < DARK_Y] = 1.0
    if ks.feather > 0:
        k = 2 * int(ks.feather) + 1
        a = cv2.GaussianBlur(a, (k, k), 0)
    return a.astype(np.float32)


def despill(bgra: np.ndarray, ks: KeySettings) -> np.ndarray:
    """A copy of `bgra` with the key colour's spill reduced (or `bgra` itself when spill is 0) (pure: tested)."""
    if ks.spill <= 0:
        return bgra
    key = hex_bgr(ks.color)
    ch = int(np.argmax(key))
    others = [c for c in range(3) if c != ch]
    out = bgra.copy()
    x = out[..., :3].astype(np.int16)
    limit = np.maximum(x[..., others[0]], x[..., others[1]])
    excess = np.clip(x[..., ch] - limit, 0, None)
    x[..., ch] -= (excess * ks.spill + 0.5).astype(np.int16)
    out[..., :3] = x.astype(np.uint8)
    return out


class ChromaKeyer:
    """Mask source with the person segmenter's interface: probability(bgra) -> (h, w) float32 foreground."""

    backend = "cpu"

    def __init__(self, settings: KeySettings):
        self.settings = settings

    def probability(self, bgra: np.ndarray) -> np.ndarray:
        h, w = bgra.shape[:2]
        s = min(1.0, KEY_SIDE / max(h, w))
        img = bgra[..., :3]
        if s < 1:
            img = cv2.resize(img, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA)
        return foreground_alpha(np.ascontiguousarray(img), self.settings)
