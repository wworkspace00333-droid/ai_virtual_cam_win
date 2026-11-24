"""Background replacement and blur behind people, for the whole frame (profile section "background").

Modes: off | blur (strength = blur size) | image (an image asset from the profile's asset library) | color (one
colour) | gradient (two colours, vertical / horizontal / diagonal) | video (a video asset, looping; frames come from
the compositor's player pool, decoded on its own thread). Quality: fast (light temporal smoothing, crisp edge),
balanced (default), quality (stronger smoothing, and the mask is refined against the picture's own edges with a
guided filter once per mask: hair and fingers follow the picture instead of the 192 px model grid). Source: person
(PP-HumanSeg, default) or depth (Depth Anything V2 Small, vcstudio/ai/depth.py: what is nearer than the focus — the
edge setting — stays, the rest is blurred or replaced; at most 4 maps / s) or chroma (0.7.0, a green / blue / any
colour screen keyed out, vcstudio/replacement/chroma_key.py, with spill suppression on every frame). The person mask
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

MODES = ("off", "blur", "image", "color", "gradient", "video")
IMAGE_LIKE = ("image", "color", "gradient")        # drawn from one (generated or decoded) picture
QUALITY = {"fast": 0.3, "balanced": 0.5, "quality": 0.7}   # temporal smoothing: weight of the previous mask
DIRECTIONS = ("vertical", "horizontal", "diagonal")
SOURCES = ("person", "depth", "chroma")


def _num(d: dict, key: str, lo: float, hi: float, default: float) -> float:
    try:
        return float(min(hi, max(lo, float(d.get(key, default)))))
    except (TypeError, ValueError):
        return default


def _bgr(hexstr, default=(40, 40, 40)) -> tuple[int, int, int]:
    try:
        v = int(str(hexstr).lstrip("#"), 16)
        return (v & 255, (v >> 8) & 255, (v >> 16) & 255)
    except (TypeError, ValueError):
        return default


def make_fill(mode: str, c1: str, c2: str, direction: str, w: int = 320, h: int = 180) -> np.ndarray:
    """A small BGRA picture for colour / gradient backgrounds (scaled to the frame by the blend)."""
    a, b = np.array(_bgr(c1), np.float32), np.array(_bgr(c2, (200, 120, 40)), np.float32)
    out = np.empty((h, w, 4), np.uint8)
    out[..., 3] = 255
    if mode == "color":
        out[..., :3] = a.astype(np.uint8)
        return out
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    t = {"vertical": yy / max(1, h - 1), "horizontal": xx / max(1, w - 1)}.get(
        direction, (xx / max(1, w - 1) + yy / max(1, h - 1)) / 2)
    out[..., :3] = (a[None, None] * (1 - t[..., None]) + b[None, None] * t[..., None]).astype(np.uint8)
    return out


@dataclass
class BackgroundConfig:
    mode: str = "off"
    strength: int = 25
    asset_id: str | None = None
    edge: float = 0.5
    fps: float = 15.0
    color: str = "#202830"
    color2: str = "#2878c8"
    direction: str = "vertical"
    quality: str = "balanced"
    source: str = "person"        # person | depth | chroma
    key_color: str = "#00b140"    # chroma key: screen colour, similarity, smoothness, spill, feather
    key_similarity: float = 0.45
    key_smoothness: float = 0.15
    key_spill: float = 0.6
    key_feather: int = 1

    def key_settings(self):
        from vcstudio.replacement.chroma_key import KeySettings
        return KeySettings(self.key_color, self.key_similarity, self.key_smoothness, self.key_spill,
                           self.key_feather)

    @classmethod
    def from_profile(cls, body: dict) -> "BackgroundConfig":
        b = body.get("background") or {}
        mode = b.get("mode", "off")
        return cls(mode=mode if mode in MODES else "off", strength=int(min(99, max(1, b.get("strength", 25)))),
                   asset_id=b.get("asset_id"), edge=float(min(0.9, max(0.1, b.get("edge", 0.5)))),
                   fps=float(min(30, max(2, b.get("fps", 15)))), color=str(b.get("color") or "#202830"),
                   color2=str(b.get("color2") or "#2878c8"),
                   direction=b.get("direction") if b.get("direction") in DIRECTIONS else "vertical",
                   quality=b.get("quality") if b.get("quality") in QUALITY else "balanced",
                   source=b.get("source") if b.get("source") in SOURCES else "person",
                   key_color=str(b.get("key_color") or "#00b140"),
                   key_similarity=_num(b, "key_similarity", 0.05, 1.5, 0.45),
                   key_smoothness=_num(b, "key_smoothness", 0.01, 1.0, 0.15),
                   key_spill=_num(b, "key_spill", 0.0, 1.0, 0.6),
                   key_feather=int(_num(b, "key_feather", 0, 10, 1)))


class BackgroundService:
    def __init__(self, capture, assets, segmenter_factory=None):
        from vcstudio.ai.person_segmentation import BackgroundSegmenter
        self.assets = assets
        self.cfg = BackgroundConfig()
        self.seg = BackgroundSegmenter(capture, segmenter_factory=segmenter_factory)
        self.person_factory = self.seg._factory
        self.depth_factory = self._make_depth                 # tests replace it
        self._image: np.ndarray | None = None
        self._lock = threading.Lock()
        self.applied = 0
        self.error = ""
        # video backgrounds: callable (asset id, t) -> BGRA frame or None; set by the app (compositor player pool)
        self.video_frame = None

    @property
    def active(self) -> bool:
        return self._snap_active(self.cfg, self._image)

    def load_profile(self, body: dict) -> None:
        cfg = BackgroundConfig.from_profile(body)
        image = None
        if cfg.mode in ("color", "gradient"):
            image = make_fill(cfg.mode, cfg.color, cfg.color2, cfg.direction)
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
        if cfg.source == "chroma":
            ks = cfg.key_settings()
            if getattr(self, "_chroma_factory", None) is None or self._chroma_key != ks:
                from vcstudio.replacement.chroma_key import ChromaKeyer
                self._chroma_key = ks
                self._chroma_factory = lambda ks=ks: ChromaKeyer(ks)
            self.seg.use(self._chroma_factory)
        else:
            self.seg.use(self.depth_factory if cfg.source == "depth" else self.person_factory)
        self.seg.fps = min(cfg.fps, 4.0) if cfg.source == "depth" else (30.0 if cfg.source == "chroma" else cfg.fps)
        self.seg.smoothing = 0.0 if cfg.source == "chroma" else QUALITY[cfg.quality]   # a key needs no history
        if not self.active:
            self.seg.masks.clear()

    @staticmethod
    def _make_depth():
        from vcstudio.ai import depth
        if not depth.available():
            raise FileNotFoundError("depth model not downloaded (Replacement → Background → Download)")
        return depth.DepthSegmenter()

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

    def snapshot(self) -> tuple[BackgroundConfig, np.ndarray | None]:
        """(config, decoded image) as loaded now — part of an atomic SceneState (vcstudio/core/scene.py)."""
        with self._lock:
            return self.cfg, self._image

    def _resolve(self, snap) -> tuple[BackgroundConfig, np.ndarray | None]:
        return snap if snap is not None else self.snapshot()

    @staticmethod
    def _snap_active(cfg: BackgroundConfig, image) -> bool:
        if cfg.mode == "video":
            return bool(cfg.asset_id)
        return cfg.mode != "off" and (cfg.mode not in IMAGE_LIKE or image is not None)

    def _picture(self, cfg: BackgroundConfig, image, t: float):
        """(mode for the blend, background picture); video: the player's frame for output time t (None: not yet)."""
        if cfg.mode == "video":
            vf = self.video_frame
            return "image", (vf(cfg.asset_id, t) if vf is not None else None)
        if cfg.mode in IMAGE_LIKE:
            return "image", image
        return cfg.mode, None

    def despill(self, frame: Frame, snap=None) -> Frame:
        """Chroma key: the frame with the screen colour's spill reduced (both the CPU and the GPU path draw the
        person from this frame); any other source: the frame itself."""
        cfg, image = self._resolve(snap)
        if cfg.source != "chroma" or cfg.key_spill <= 0 or not self._snap_active(cfg, image):
            return frame
        if self.seg.masks.peek()[0] is None:
            return frame
        from vcstudio.replacement.chroma_key import despill
        with frame.bgra() as img:
            out = despill(img, cfg.key_settings())
        return Frame(frame.seq, frame.pts, frame.arrival, frame.width, frame.height, array=out,
                     source=f"{frame.source}+despill")

    def apply(self, frame: Frame, snap=None) -> Frame:
        """CPU path: the frame with its background blurred / replaced (OpenCV blend), or the frame itself.
        `snap` = (config, image) of the scene being drawn; default: the service's current state."""
        cfg, image = self._resolve(snap)
        if not self._snap_active(cfg, image):
            return frame
        mask, ver = self.seg.masks.peek()
        if mask is None:
            return frame
        mode, pic = self._picture(cfg, image, frame.pts)
        if mode == "image" and pic is None:
            return frame                                               # video not decoded yet: frame unchanged
        from vcstudio.ai.person_segmentation import composite_background
        with frame.bgra() as img:
            out = composite_background(img, mask, mode, cfg.strength, pic, cfg.edge,
                                       mask_key=(id(self), ver),       # same mask version: reuse its blend weights
                                       refine=cfg.quality == "quality")
        self.applied += 1
        return Frame(frame.seq, frame.pts, frame.arrival, frame.width, frame.height, array=out,
                     source=f"{frame.source}+background")

    def ops(self, frame: Frame, snap=None) -> list:
        """Compositor layers that draw the background (empty: nothing to do / no mask yet).

        One "composite" layer over the whole frame: the source is a blurred quarter-size copy of the frame (blur) or
        the chosen image (image), scaled to the frame by the sampler; the layer's mask is "not a person". The GPU
        backend (Direct3D 11 / Metal) and the CPU backend run the same layer code (parity-tested), so the costly
        full-size blend runs on the GPU when there is one; the CPU only blurs a quarter-size image."""
        cfg, image = self._resolve(snap)
        if not self._snap_active(cfg, image):
            return []
        mask, ver = self.seg.masks.peek()
        if mask is None:
            return []
        import cv2

        from vcstudio.replacement.compositor import DrawOp
        W, H = frame.width, frame.height
        lo, hi = max(0.0, cfg.edge - 0.25), min(1.0, cfg.edge + 0.25)
        person = np.clip((mask - lo) / max(1e-6, hi - lo), 0.0, 1.0)
        not_person = np.ascontiguousarray(((1.0 - person) * 255 + 0.5).astype(np.uint8))
        mode, pic = self._picture(cfg, image, frame.pts)
        if mode == "image" and pic is None:
            return []
        if mode == "blur":
            with frame.bgra() as img:
                small = cv2.resize(img, (max(1, W // 4), max(1, H // 4)), interpolation=cv2.INTER_AREA)
            src = cv2.GaussianBlur(small, (0, 0), sigmaX=max(0.5, (cfg.strength | 1) / 4))
            src[..., 3] = 255                                           # opaque (premultiplied = straight)
            src_key: tuple = ("bgblur", frame.seq)                      # changes every frame: uploaded per frame
        else:
            src = pic
            src_key = (("bgvid", cfg.asset_id, frame.seq) if cfg.mode == "video" else      # new picture per frame
                       ("img", "background", cfg.mode, cfg.asset_id, cfg.color, cfg.color2, cfg.direction))
        sh, sw = src.shape[:2]
        sx, sy = sw / W, sh / H
        inv = np.array([[sx, 0.0, 0.5 * sx - 0.5], [0.0, sy, 0.5 * sy - 0.5]])   # frame px -> src px (pixel centres)
        self.applied += 1
        return [DrawOp("__background__", "composite", (0, 0, W, H), 1.0, mask=not_person,
                       mask_rect=(0.0, 0.0, float(W), float(H)), mask_key=("bgmask", ver), src=np.ascontiguousarray(src),
                       src_key=src_key, inv=inv)]
