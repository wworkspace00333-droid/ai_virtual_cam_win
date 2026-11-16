"""Open-vocabulary detection ("any object" by a text description) and promptable masks.

OpenVocabDetector — OWLv2 base patch16 ensemble (Google, Apache-2.0), 8-bit ONNX export (Xenova, Transformers.js):
  inputs  input_ids / attention_mask (CLIP tokens, 16 per query) and pixel_values 1x3x960x960;
  outputs logits (1, 3600, queries) and pred_boxes (1, 3600, 4) as cx, cy, w, h relative to the padded square image.
  Pre-processing as Owlv2ImageProcessor: pad to a square at the bottom / right with grey 0.5, resize to 960,
  CLIP mean / std. Score = sigmoid(logit). Detections carry the query text as label, so ROIs pick objects by it.
  Text embeddings are cached per query list (the text side runs once per change, not per frame... the export
  always runs both towers, so the text is short: 16 tokens).
  Image queries ("find things like this picture") are not in this export: they need OWLv2's class head, which the
  ONNX graph does not expose. Text and click (SAM) are supported.

PromptSegmenter — SAM 2.1 hiera-tiny (Meta, Apache-2.0), 8-bit ONNX (onnx-community): a 1024x1024 image encoder and
  a prompt decoder taking points (label 1 = object, 0 = background) and boxes in 1024-space; masks come at 256x256
  as logits, 3 candidates with predicted IoU; the best is upscaled to the box and thresholded at 0.

Both run on ONNX Runtime with the app's provider order (CUDA / DirectML / CPU) and the optimised-model cache.
On the 2-core development server (CPU) one OWLv2 run takes seconds: correctness and accuracy are checked there,
real-time use needs a GPU (docs/AUDIT_WIN.md).
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import cv2
import numpy as np

from vcstudio.ai.detection import Detection

log = logging.getLogger(__name__)

OWL_FILE = "owlv2_base_ensemble_int8.onnx"
OWL_TOKENIZER = "owlv2_tokenizer.json"
SAM_DIR = "sam2.1_tiny"
OWL_SIZE = 960
OWL_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], np.float32)
OWL_STD = np.array([0.26862954, 0.26130258, 0.27577711], np.float32)
SAM_SIZE = 1024
SAM_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
SAM_STD = np.array([0.229, 0.224, 0.225], np.float32)
MAX_TOKENS = 16


def models_dir() -> Path:
    from vcstudio.core.resources import models_dir as md
    return md()


def available() -> bool:
    d = models_dir()
    return (d / OWL_FILE).exists() and (d / OWL_TOKENIZER).exists()


def sam_available() -> bool:
    d = models_dir() / SAM_DIR
    return (d / "vision_encoder_int8.onnx").exists() and (d / "prompt_encoder_mask_decoder_int8.onnx").exists()


class ClipTokenizer:
    """CLIP byte-level BPE from the model's tokenizer.json, in plain Python (no tokenizers / huggingface_hub
    dependency: those bring network code and MPL-licensed parts into the app for a few lines of work).

    NFC, collapse whitespace, lowercase; split into words / single digits / punctuation runs; GPT-2 byte-to-unicode
    mapping; BPE with "</w>" on the last symbol of each word; <|startoftext|> ... <|endoftext|>; padded with id 0 and
    truncated to `length` tokens like the reference tokenizer (tests compare token ids)."""

    _SPLIT = None

    def __init__(self, path: Path, length: int = MAX_TOKENS):
        import json
        import re
        t = json.loads(Path(path).read_text(encoding="utf-8"))
        m = t["model"]
        self.vocab: dict[str, int] = m["vocab"]
        merges = [tuple(x.split(" ")) if isinstance(x, str) else tuple(x) for x in m["merges"]]
        self.ranks = {p: i for i, p in enumerate(merges)}
        self.bos, self.eos = self.vocab["<|startoftext|>"], self.vocab["<|endoftext|>"]
        self.length = length
        bs = list(range(ord("!"), ord("~") + 1)) + list(range(ord("¡"), ord("¬") + 1)) + list(range(ord("®"), ord("ÿ") + 1))
        cs, n = bs[:], 0
        for b in range(256):
            if b not in bs:
                bs.append(b); cs.append(256 + n); n += 1
        self.byte_map = {b: chr(c) for b, c in zip(bs, cs)}
        # \p{L}+ | \p{N} | [^\s\p{L}\p{N}]+  (Python's re: letters = [^\W\d_], numbers = \d)
        self.split = re.compile(r"'s|'t|'re|'ve|'m|'ll|'d|[^\W\d_]+|\d|(?:[^\s\w]|_)+")
        self._cache: dict[str, list[str]] = {}

    def _bpe(self, word: str) -> list[str]:
        if word in self._cache:
            return self._cache[word]
        syms = list(word[:-1]) + [word[-1] + "</w>"]
        while len(syms) > 1:
            pairs = [(self.ranks.get((a, b), 1 << 30), i) for i, (a, b) in enumerate(zip(syms, syms[1:]))]
            rank, i = min(pairs)
            if rank == 1 << 30:
                break
            syms = syms[:i] + [syms[i] + syms[i + 1]] + syms[i + 2:]
        self._cache[word] = syms
        return syms

    def encode(self, text: str) -> tuple[list[int], list[int]]:
        import unicodedata
        s = " ".join(unicodedata.normalize("NFC", text).split()).lower()
        body: list[int] = []
        for piece in self.split.findall(s):
            word = "".join(self.byte_map[b] for b in piece.encode("utf-8"))
            body += [self.vocab.get(tok, self.eos) for tok in self._bpe(word)]
        # truncation keeps room for the start / end tokens (as the reference post-processor does). Known
        # difference: a run of punctuation ("!!") is one BPE word here, two bare tokens in the reference.
        ids = [self.bos] + body[:self.length - 2] + [self.eos]
        mask = [1] * len(ids) + [0] * (self.length - len(ids))
        return ids + [0] * (self.length - len(ids)), mask


def _nms(boxes: np.ndarray, scores: np.ndarray, iou: float) -> list[int]:
    from vcstudio.ai.segmentation import nms
    return nms(boxes, scores, iou)


class OpenVocabDetector:
    """detect(frame, conf, iou) -> (detections, ms), like the other detectors; labels are the query texts."""
    name = "owlv2-base"

    def __init__(self, queries: list[str], compute_units: str = "all", cache_dir: Path | None = None):
        from vcstudio.ai.onnx_engine import create_session
        d = models_dir()
        if not available():
            raise FileNotFoundError(f"open-vocabulary model missing in {d} (scripts/fetch_models.py --open-vocab)")
        self.sess, self.backend = create_session(d / OWL_FILE, compute_units, cache_dir)
        self.tok = ClipTokenizer(d / OWL_TOKENIZER)
        self.set_queries(queries)
        self._canvas = np.empty((OWL_SIZE, OWL_SIZE, 3), np.float32)

    def set_queries(self, queries: list[str]) -> None:
        qs = [q.strip() for q in queries if q and q.strip()]
        if not qs:
            raise ValueError("at least one text query is needed")
        self.queries = qs
        enc = [self.tok.encode(q) for q in qs]
        self._ids = np.array([ids for ids, _ in enc], np.int64)
        self._mask = np.array([m for _, m in enc], np.int64)

    def _blob(self, bgra: np.ndarray) -> tuple[np.ndarray, int]:
        h, w = bgra.shape[:2]
        side = max(h, w)
        r = OWL_SIZE / side
        nw, nh = max(1, round(w * r)), max(1, round(h * r))
        rgb = cv2.cvtColor(cv2.resize(bgra[..., :3], (nw, nh), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)
        c = self._canvas
        c[...] = 0.5                                       # padding grey (after rescaling to 0..1)
        c[:nh, :nw] = rgb.astype(np.float32) / 255.0
        x = (c - OWL_MEAN) / OWL_STD
        return np.ascontiguousarray(x.transpose(2, 0, 1)[None]), side

    def detect(self, frame, conf: float = 0.1, iou: float = 0.3) -> tuple[list[Detection], float]:
        t0 = time.perf_counter()
        with frame.bgra() as img:
            blob, side = self._blob(img)
        logits, boxes = self.sess.run(["logits", "pred_boxes"],
                                      {"input_ids": self._ids, "attention_mask": self._mask, "pixel_values": blob})[:2]
        scores = 1.0 / (1.0 + np.exp(-logits[0]))         # (3600, queries)
        cls = scores.argmax(1)
        best = scores[np.arange(len(scores)), cls]
        m = best >= conf
        dets: list[Detection] = []
        if m.any():
            b = boxes[0][m] * side                         # normalised to the padded square -> original pixels
            xyxy = np.stack([b[:, 0] - b[:, 2] / 2, b[:, 1] - b[:, 3] / 2, b[:, 0] + b[:, 2] / 2,
                             b[:, 1] + b[:, 3] / 2], 1)
            sc, cl = best[m], cls[m]
            keep = _nms(xyxy + cl[:, None] * 1e5, sc, iou)[:100]
            W, H = frame.width, frame.height
            for i in keep:
                x0, y0, x1, y1 = (float(np.clip(xyxy[i, 0], 0, W)), float(np.clip(xyxy[i, 1], 0, H)),
                                  float(np.clip(xyxy[i, 2], 0, W)), float(np.clip(xyxy[i, 3], 0, H)))
                if x1 - x0 >= 2 and y1 - y0 >= 2:
                    dets.append(Detection(self.queries[int(cl[i])], float(sc[i]), x0, y0, x1, y1, int(cl[i]), "owlv2"))
        return dets, (time.perf_counter() - t0) * 1000


class PromptSegmenter:
    """SAM 2.1 tiny: encode a frame once, then masks for boxes or clicks (frame pixel coordinates)."""
    name = "sam2.1-tiny"

    def __init__(self, compute_units: str = "all", cache_dir: Path | None = None):
        from vcstudio.ai.onnx_engine import create_session
        d = models_dir() / SAM_DIR
        if not sam_available():
            raise FileNotFoundError(f"SAM 2.1 model missing in {d} (scripts/fetch_models.py)")
        # the encoder keeps its weights in an external .onnx_data file next to it: no optimised-model cache
        self.enc, self.backend = create_session(d / "vision_encoder_int8.onnx", compute_units)
        self.dec, _ = create_session(d / "prompt_encoder_mask_decoder_int8.onnx", compute_units)
        self._emb: list[np.ndarray] | None = None
        self._size = (0, 0)
        self.encode_ms = 0.0

    def set_image(self, bgra: np.ndarray) -> None:
        t0 = time.perf_counter()
        h, w = bgra.shape[:2]
        rgb = cv2.cvtColor(cv2.resize(bgra[..., :3], (SAM_SIZE, SAM_SIZE), interpolation=cv2.INTER_LINEAR),
                           cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        x = np.ascontiguousarray(((rgb - SAM_MEAN) / SAM_STD).transpose(2, 0, 1)[None])
        self._emb = self.enc.run(None, {"pixel_values": x})
        self._size = (w, h)
        self.encode_ms = (time.perf_counter() - t0) * 1000

    def _decode(self, points: np.ndarray, labels: np.ndarray, boxes: np.ndarray) -> tuple[np.ndarray, float]:
        if self._emb is None:
            raise RuntimeError("set_image first")
        w, h = self._size
        sx, sy = SAM_SIZE / w, SAM_SIZE / h
        feeds = {"input_points": (points * [sx, sy]).astype(np.float32)[None, None],
                 "input_labels": labels.astype(np.int64)[None, None],
                 "input_boxes": (boxes * [sx, sy, sx, sy]).astype(np.float32)[None],
                 "image_embeddings.0": self._emb[0], "image_embeddings.1": self._emb[1],
                 "image_embeddings.2": self._emb[2]}
        iou, masks = self.dec.run(["iou_scores", "pred_masks"], feeds)[:2]
        k = int(iou[0, 0].argmax())
        low = masks[0, 0, k]                               # 256x256 logits over the whole (squashed) frame
        full = cv2.resize(low.astype(np.float32), (w, h), interpolation=cv2.INTER_LINEAR)
        return full > 0.0, float(iou[0, 0, k])

    def mask_from_box(self, box: tuple[float, float, float, float]) -> tuple[np.ndarray, float]:
        """(H, W) bool mask of the object in the box, and SAM's predicted IoU."""
        empty_pts = np.zeros((0, 2), np.float32)
        return self._decode(empty_pts, np.zeros((0,), np.int64), np.array([box], np.float32))

    def mask_from_click(self, x: float, y: float) -> tuple[np.ndarray, float]:
        """Mask of the object under a click (the "click on the object" target)."""
        return self._decode(np.array([[x, y]], np.float32), np.array([1], np.int64), np.zeros((0, 4), np.float32))


def box_of(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def local_mask(full: np.ndarray, box: tuple[float, float, float, float], max_side: int = 160) -> np.ndarray:
    """Frame-size bool mask -> uint8 box-local silhouette, the format of Detection.mask (as YOLO-seg produces)."""
    from vcstudio.ai.segmentation import MASK_MAX
    max_side = MASK_MAX if MASK_MAX else max_side
    x0, y0, x1, y1 = box
    h, w = full.shape
    ix0, iy0 = max(0, int(np.floor(x0))), max(0, int(np.floor(y0)))
    ix1, iy1 = min(w, int(np.ceil(x1))), min(h, int(np.ceil(y1)))
    crop = full[iy0:iy1, ix0:ix1].astype(np.uint8) * 255
    bw, bh = x1 - x0, y1 - y0
    scale = min(1.0, max_side / max(bw, bh, 1.0))
    ow, oh = max(2, int(round(bw * scale))), max(2, int(round(bh * scale)))
    if crop.size == 0:
        return np.zeros((oh, ow), np.uint8)
    return cv2.resize(crop, (ow, oh), interpolation=cv2.INTER_LINEAR)


class OpenVocabPipeline:
    """The AI engine's detector for "any object": OWLv2 boxes for the text queries, and with masks=True a SAM 2.1
    silhouette per box (object replacement needs it). Same interface as the other detectors."""

    def __init__(self, queries: list[str], compute_units: str = "all", cache_dir: Path | None = None,
                 masks: bool = False):
        self.owl = OpenVocabDetector(queries, compute_units, cache_dir)
        self.sam = PromptSegmenter(compute_units) if masks and sam_available() else None
        self.masks = self.sam is not None
        self.name = "owlv2-base" + ("+sam2.1" if self.masks else "")
        self.backend = self.owl.backend
        self.queries = self.owl.queries

    def detect(self, frame, conf: float = 0.1, iou: float = 0.3) -> tuple[list[Detection], float]:
        t0 = time.perf_counter()
        dets, _ = self.owl.detect(frame, conf, iou)
        if self.sam is not None and dets:
            with frame.bgra() as img:
                self.sam.set_image(img)
            out = []
            for d in dets:
                full, _q = self.sam.mask_from_box((d.x0, d.y0, d.x1, d.y1))
                out.append(Detection(d.label, d.confidence, d.x0, d.y0, d.x1, d.y1, d.class_id, "owlv2+sam",
                                     mask=local_mask(full, (d.x0, d.y0, d.x1, d.y1))))
            dets = out
        return dets, (time.perf_counter() - t0) * 1000
