"""Person (foreground) segmentation for background replacement and blur.

Model: PP-HumanSeg (PaddlePaddle, Apache-2.0), the OpenCV Zoo ONNX port human_segmentation_pphumanseg_2023mar.onnx:
input 1x3x192x192 RGB, (x / 255 - 0.5) / 0.5; output 1x2x192x192 class scores (0 background, 1 person).
The person probability is upscaled to the frame and smoothed over time, so the edge does not flicker.

A custom model (AI Models -> Use in the app, role "background") may have another layout; seg_layout() reads it from
the output shape of a real run:
  two-class      (1, 2, H, W) or (1, H, W, 2): background / person scores (PP-HumanSeg and alike), softmax
  matte          (1, 1, H, W), (1, H, W, 1) or (1, H, W): one person map (alpha matting, selfie segmentation);
                 values outside 0..1 are taken as logits (sigmoid)
  semantic       (1, C, H, W) or (1, H, W, C) with the class list told by C: 21 Pascal VOC (person 15),
                 19 Cityscapes (person 11), 150 ADE20K (person 12); softmax, the person class
Input: N x C x H x W float is scaled to (x / 255 - 0.5) / 0.5, N x H x W x C float to x / 255 (the conventions of
PaddleSeg / MODNet and of TensorFlow / MediaPipe exports), uint8 inputs get the pixels as they are; a dynamic size is
run at DYNAMIC_SIDE. A model trained with other scaling still runs, with a less exact mask.

BackgroundSegmenter runs on its own thread at a limited rate and keeps only the newest mask (LatestSlot): the camera
output never waits for it; the pipeline blends the newest mask into every frame.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from vcstudio.core.frame_queue import LatestSlot

log = logging.getLogger(__name__)

MODEL_FILE = "human_segmentation_pphumanseg_2023mar.onnx"
SIZE = 192
DYNAMIC_SIDE = 320                 # the size model_check runs dynamic models at, so the check matches the use
PERSON_CLASS = {21: (15, "Pascal VOC"), 19: (11, "Cityscapes"), 150: (12, "ADE20K")}


@dataclass(frozen=True)
class SegLayout:
    kind: str                      # "two_class" | "matte" | "semantic"
    channels_last: bool            # the map is (1, H, W, C)
    channel: int                   # channel with the person score (0 for a matte)
    name: str                      # class list of a semantic map

    def describe(self) -> str:
        return {"two_class": "2-class map", "matte": "1-channel person map",
                "semantic": f"{self.name} classes, person {self.channel}"}[self.kind]


def seg_layout(shape) -> SegLayout | None:
    """The person-map layout of one output shape (batch 1), or None (pure: tested)."""
    s = [int(d) if d else 0 for d in shape]
    if len(s) == 3 and s[0] == 1 and s[1] >= 16 and s[2] >= 16 and max(s[1:]) <= 4 * min(s[1:]):   # not (1, 84, N)
        return SegLayout("matte", False, 0, "")
    if len(s) != 4 or s[0] != 1:
        return None

    def of(c: int, last: bool) -> SegLayout | None:
        if c == 1:
            return SegLayout("matte", last, 0, "")
        if c == 2:
            return SegLayout("two_class", last, 1, "")
        if c in PERSON_CLASS:
            return SegLayout("semantic", last, PERSON_CLASS[c][0], PERSON_CLASS[c][1])
        return None

    if s[2] >= 16 and s[3] >= 16 and (lay := of(s[1], False)) is not None:
        return lay
    if s[1] >= 16 and s[2] >= 16 and (lay := of(s[3], True)) is not None:
        return lay
    return None


def person_map(out: np.ndarray, lay: SegLayout) -> np.ndarray:
    """(H, W) float32 person probability from one output (batch included) of layout `lay` (pure: tested)."""
    a = np.asarray(out, np.float32)[0]
    if a.ndim == 2:
        a = a[None]
    elif lay.channels_last:
        a = np.moveaxis(a, -1, 0)
    if lay.kind == "matte":
        m = a[0]
        if float(m.min()) < -1e-3 or float(m.max()) > 1 + 1e-3:
            m = 1.0 / (1.0 + np.exp(-m))
        return np.ascontiguousarray(m, np.float32)
    e = np.exp(a - a.max(0, keepdims=True))
    return (e[lay.channel] / e.sum(0)).astype(np.float32)


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
        inp = self.sess.get_inputs()[0]
        self.input = inp.name
        d = [x if isinstance(x, int) and x > 0 else None for x in inp.shape]
        if len(d) != 4:
            raise ValueError(f"segmentation model needs an image input (N x C x H x W or N x H x W x C), got {d}")
        self.nhwc = not (d[1] in (1, 3, 4) or (d[1] is None and d[3] not in (1, 3, 4)))
        self.channels = (d[3] if self.nhwc else d[1]) or 3
        self.h = (d[1] if self.nhwc else d[2]) or DYNAMIC_SIDE
        self.w = (d[2] if self.nhwc else d[3]) or DYNAMIC_SIDE
        self.uint8 = "uint8" in str(inp.type)
        self.out_index, self.layout = self._probe()

    def _tensor(self, bgr: np.ndarray) -> np.ndarray:
        small = cv2.resize(bgr, (self.w, self.h), interpolation=cv2.INTER_AREA)
        if self.channels == 1:
            img = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)[..., None]
        else:
            img = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        if self.uint8:
            x = img
        elif self.nhwc:
            x = img.astype(np.float32) / 255.0
        else:
            x = (img.astype(np.float32) / 255.0 - 0.5) / 0.5
        if self.channels == 4:
            x = np.concatenate([x, np.full_like(x[..., :1], 255 if self.uint8 else 1)], -1)
        x = x[None] if self.nhwc else x.transpose(2, 0, 1)[None]
        return np.ascontiguousarray(x)

    def _probe(self) -> tuple[int, SegLayout]:
        """Which output is the person map, and its layout (one run on a grey picture)."""
        outs = self.sess.run(None, {self.input: self._tensor(np.full((self.h, self.w, 3), 128, np.uint8))})
        for i, o in enumerate(outs):
            lay = seg_layout(np.asarray(o).shape)
            if lay is not None:
                return i, lay
        raise ValueError("no person map among the outputs " + ", ".join(str(np.asarray(o).shape) for o in outs))

    def probability(self, bgra: np.ndarray) -> np.ndarray:
        """Person probability (h, w) float32 0..1 at the model's output size."""
        out = self.sess.run(None, {self.input: self._tensor(np.ascontiguousarray(bgra[..., :3]))})[self.out_index]
        return person_map(out, self.layout)


def available() -> bool:
    return model_path().exists()


class PersonMaskDetector:
    """Silhouettes of people out of the box: the COCO detector's boxes, and a PP-HumanSeg mask on each "person" box
    (the shipped background model, ~3 ms per box on a CPU), so object replacement works for people without importing
    a segmentation model or running SAM 2.1 (seconds per frame without a GPU). Each box is cropped with a margin and
    segmented on its own, which gives far finer edges than one 192x192 pass over the frame. Other classes keep their
    boxes without a mask (object regions skip them: no fake silhouette)."""

    LABEL = "person"
    PAD = 0.12                                        # crop margin per side, of the box size: context for the edge

    def __init__(self, detector, compute_units: str = "all", max_masks: int = 4, segmenter=None):
        self.det = detector
        self.seg = segmenter if segmenter is not None else PersonSegmenter(compute_units=compute_units)
        self.max_masks = max_masks
        self.name = f"{detector.name}+pphumanseg"
        self.loaded = getattr(detector, "loaded", None)
        self.backend = getattr(detector, "backend", "")
        self.masked = 0                               # masks made in the last frame (stats / tests)

    def mask_for(self, img: np.ndarray, box: tuple[float, float, float, float]) -> np.ndarray | None:
        from vcstudio.ai.segmentation import MASK_MAX
        H, W = img.shape[:2]
        x0, y0, x1, y1 = box
        bw, bh = x1 - x0, y1 - y0
        if bw < 4 or bh < 4:
            return None
        cx0, cy0 = max(0, int(x0 - bw * self.PAD)), max(0, int(y0 - bh * self.PAD))
        cx1, cy1 = min(W, int(np.ceil(x1 + bw * self.PAD))), min(H, int(np.ceil(y1 + bh * self.PAD)))
        if cx1 - cx0 < 4 or cy1 - cy0 < 4:
            return None
        p = self.seg.probability(img[cy0:cy1, cx0:cx1])                  # e.g. (192, 192) over the crop
        # box inside the crop, in the probability map's pixels; sample it at the box-local mask resolution
        sx, sy = p.shape[1] / (cx1 - cx0), p.shape[0] / (cy1 - cy0)
        scale = min(1.0, MASK_MAX / max(bw, bh))
        ow, oh = max(2, int(round(bw * scale))), max(2, int(round(bh * scale)))
        M = np.array([[(bw * sx) / ow, 0, (x0 - cx0) * sx], [0, (bh * sy) / oh, (y0 - cy0) * sy]], np.float32)
        local = cv2.warpAffine(p, M, (ow, oh), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                               borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        return np.clip(local * 255 + 0.5, 0, 255).astype(np.uint8)

    def detect(self, frame, conf: float = 0.25, iou: float = 0.5):
        from vcstudio.ai.detection import Detection
        t0 = time.perf_counter()
        dets, _ = self.det.detect(frame, conf, iou)
        want = sorted((i for i, d in enumerate(dets) if d.label == self.LABEL),
                      key=lambda i: -dets[i].confidence)[: self.max_masks]
        self.masked = 0
        if want:
            with frame.bgra() as img:
                for i in want:
                    d = dets[i]
                    m = self.mask_for(img, (d.x0, d.y0, d.x1, d.y1))
                    if m is None:
                        continue
                    dets[i] = Detection(d.label, d.confidence, d.x0, d.y0, d.x1, d.y1, d.class_id,
                                        f"{d.source}+pphumanseg", mask=m)
                    self.masked += 1
        return dets, (time.perf_counter() - t0) * 1000


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

    def use(self, factory) -> None:
        """Switch what produces the masks (person segmentation <-> depth); restarts the thread if it runs."""
        if factory is self._factory:
            return
        running = self.running
        if running:
            self.stop()
        self._factory, self._seg = factory, None
        self.masks.clear()
        if running:
            self.start()

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


# full-size blend weights of the last mask: (key, m, inv). The mask changes at the segmentation rate (5-15 /s), the
# frames at 30-60 /s, so most frames reuse them (two 1080p float resizes, ~4.5 ms on the development server).
_weights_cache: tuple | None = None


GUIDE_SIDE = 480                   # the guided filter runs at this long side (Quality mode)


def guided_refine(mask: np.ndarray, guide_bgr: np.ndarray, radius: int = 6, eps: float = 1e-3) -> np.ndarray:
    """Edge-aware mask refinement (guided filter, He et al. 2010, grey guide): the person mask follows the picture's
    own edges — hair, shoulders, fingers — instead of the 192x192 model grid. Runs at GUIDE_SIDE (box filters only;
    ~4 ms on the 2-core server). `mask` float 0..1 of any size; returns float32 0..1 at the guide's size."""
    gh, gw = guide_bgr.shape[:2]
    k = GUIDE_SIDE / max(gw, gh)
    sw, sh = max(8, int(gw * k)), max(8, int(gh * k))
    I = cv2.cvtColor(cv2.resize(guide_bgr, (sw, sh), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    I = I.astype(np.float32) / 255.0
    P = cv2.resize(np.asarray(mask, np.float32), (sw, sh), interpolation=cv2.INTER_LINEAR)
    box = (2 * radius + 1, 2 * radius + 1)
    mI, mP = cv2.boxFilter(I, -1, box), cv2.boxFilter(P, -1, box)
    cov = cv2.boxFilter(I * P, -1, box) - mI * mP
    var = cv2.boxFilter(I * I, -1, box) - mI * mI
    a = cov / (var + eps)
    b = mP - a * mI
    q = cv2.boxFilter(a, -1, box) * I + cv2.boxFilter(b, -1, box)
    return np.clip(q, 0.0, 1.0)


def _weights(mask192: np.ndarray, edge: float, w: int, h: int, key, guide=None) -> tuple[np.ndarray, np.ndarray]:
    global _weights_cache
    ck = (key, round(float(edge), 4), w, h, guide is not None)
    c = _weights_cache
    if key is not None and c is not None and c[0] == ck:
        return c[1], c[2]
    # edge remap and 1 - m on the small mask, then both weights scaled up: the same order as the GPU layer (mask
    # remapped, then sampled bilinearly); remapping 2 M full-size values cost ~11 of the ~24 ms per 1080p frame
    lo, hi = max(0.0, edge - 0.25), min(1.0, edge + 0.25)
    small_m = np.clip((np.asarray(mask192, np.float32) - lo) / max(1e-6, hi - lo), 0.0, 1.0)
    if guide is not None:                              # Quality: refine against the picture's edges first
        small_m = guided_refine(small_m, guide)
    m = cv2.resize(small_m, (w, h), interpolation=cv2.INTER_LINEAR)
    inv = cv2.resize(1.0 - small_m, (w, h), interpolation=cv2.INTER_LINEAR)
    if key is not None:
        _weights_cache = (ck, m, inv)                  # one tuple: readers on other threads see old or new, whole
    return m, inv


def blur_scale(strength: int) -> int:
    """Downscale factor for the background blur: 1/8 once the blur is wide enough (sigma >= 3 px there, 24 px at full
    size) that a bilinear upscale cannot show the coarser grid; 1/4 below. ~2.5 ms less per 1080p frame."""
    return 8 if int(strength) >= 24 else 4


def composite_background(frame_bgra: np.ndarray, mask192: np.ndarray, mode: str, strength: int = 25,
                         image_bgra: np.ndarray | None = None, edge: float = 0.5,
                         out: np.ndarray | None = None, mask_key=None, refine: bool = False) -> np.ndarray:
    """Blend: person from the frame, background blurred (mode "blur") or replaced (mode "image").

    The mask is upscaled with a soft edge: values are remapped around `edge` (0..1) so the outline is crisp but not
    aliased. Blur runs on a 1/4 or 1/8 size copy (much cheaper than a full-size Gaussian, visually the same for a
    background). `mask_key` (e.g. the mask's version number) lets frames that share a mask reuse its upscaled blend
    weights. `refine` (Quality mode) runs the guided filter against this frame once per mask version.
    Returns a new BGRA array (or `out`, written in place)."""
    h, w = frame_bgra.shape[:2]
    if mode not in ("blur", "image") or (mode == "image" and image_bgra is None):
        return frame_bgra
    m, inv = _weights(mask192, edge, w, h, mask_key, frame_bgra[..., :3] if refine else None)
    if mode == "blur":
        k = max(1, int(strength)) | 1
        f = blur_scale(strength)
        small = cv2.resize(frame_bgra, (max(1, w // f), max(1, h // f)), interpolation=cv2.INTER_AREA)
        small = cv2.GaussianBlur(small, (0, 0), sigmaX=max(0.5, k / f))
        bg = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
    else:
        assert image_bgra is not None
        bg = image_bgra if image_bgra.shape[:2] == (h, w) else cv2.resize(image_bgra, (w, h), interpolation=cv2.INTER_AREA)
    src = np.ascontiguousarray(frame_bgra)
    if out is not None and out.shape == src.shape and out.dtype == src.dtype and out.flags.c_contiguous \
            and not np.shares_memory(out, src):
        cv2.blendLinear(src, np.ascontiguousarray(bg), m, inv, dst=out)              # SIMD, uint8, no extra copy
        return out
    res = cv2.blendLinear(src, np.ascontiguousarray(bg), m, inv)
    if out is None:
        return res
    np.copyto(out, res)
    return out
