"""CLIP ViT-B/32 (OpenAI, MIT), 8-bit ONNX (Xenova): a second opinion on what OWLv2 found, and objects by picture.

EXPERIMENTAL, not used by the app. Measured on coco16 (2026-11-17, docs/ACCURACY.md): zero-shot top-1 on ground-truth
crops 25 % (80 names; 42-48 % for objects larger than 96 px), so the check removes more right boxes than wrong
ones (OWLv2 mAP50 0.589 -> 0.308 filtered at p >= 0.05, 0.541 re-ranked by conf x sqrt(p)); picture-to-picture
retrieval of the same class: mean AP 0.31, precision 0.04-0.07 at any threshold. Kept with scripts/eval_open_vocab.py
--clip so a better model (full precision, larger ViT) can be measured the same way before it reaches the UI.

ClipEncoder   image crops and texts -> unit vectors in one 512-d space (cosine similarity = dot product).
ClipVerifier  keeps an OWLv2 detection only if CLIP, shown the crop, agrees it is the query (zero-shot over the
              queries plus a few "not an object" prompts). Removes the confident-but-wrong boxes OWLv2 makes on
              textures and parts of other objects.
ImageQuery    "find things like this picture": the example pictures' embedding is compared with each candidate box
              (OWLv2 proposals for a generic prompt); boxes close enough are labelled with the query's name.
              OWLv2's own image-guided head is not in the ONNX export, so CLIP does the matching.

Pre-processing as CLIPImageProcessor: shortest side to 224 (bicubic), centre crop 224, RGB, CLIP mean / std.
The text model reads the CLIP tokens that come with OWLv2 (same vocabulary), 77 tokens, padded with <|endoftext|>;
the export pools at the first end token, so padding does not change the result.
Models: <app data>\\models\\clip (button "Download CLIP" or scripts/fetch_models.py --clip), about 147 MB.
"""
from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np

from vcstudio.ai.detection import Detection

CLIP_DIR = "clip"
VISION = "vision_model_int8.onnx"
TEXT = "text_model_int8.onnx"
SIZE = 224
MEAN = np.array([0.48145466, 0.4578275, 0.40821073], np.float32)
STD = np.array([0.26862954, 0.26130258, 0.27577711], np.float32)
TOKENS = 77
TEMPLATE = "a photo of a {}."
# what a wrong box usually shows: part of something else, a texture, the background
NEGATIVES = ("a photo of a background.", "a photo of a texture.", "a blurry photo of something.")
LOGIT_SCALE = 100.0                     # CLIP's learned temperature (exp(4.6052) for ViT-B/32)
PROPOSAL_QUERY = "an object"            # OWLv2 prompt that yields candidate boxes for picture queries


def models_dir() -> Path:
    from vcstudio.ai.open_vocab import models_dir as ov_dir
    return ov_dir() / CLIP_DIR


def available() -> bool:
    from vcstudio.ai import open_vocab
    d = models_dir()
    return (d / VISION).exists() and (d / TEXT).exists() and open_vocab.available()


def _unit(x: np.ndarray) -> np.ndarray:
    return x / np.maximum(np.linalg.norm(x, axis=-1, keepdims=True), 1e-12)


def preprocess(bgr: np.ndarray) -> np.ndarray:
    """(H, W, 3|4) uint8 BGR(A) -> (3, 224, 224) float32, as CLIPImageProcessor."""
    h, w = bgr.shape[:2]
    s = SIZE / min(h, w)
    nw, nh = max(SIZE, round(w * s)), max(SIZE, round(h * s))
    img = cv2.resize(np.ascontiguousarray(bgr[..., :3]), (nw, nh), interpolation=cv2.INTER_CUBIC)
    x0, y0 = (nw - SIZE) // 2, (nh - SIZE) // 2
    rgb = cv2.cvtColor(img[y0:y0 + SIZE, x0:x0 + SIZE], cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    return ((rgb - MEAN) / STD).transpose(2, 0, 1)


def crop(img: np.ndarray, d, pad: float = 0.1) -> np.ndarray | None:
    """The detection's box (with a little context around it) from a frame-size image; None if it is empty."""
    h, w = img.shape[:2]
    bw, bh = d.x1 - d.x0, d.y1 - d.y0
    x0, y0 = int(max(0, d.x0 - pad * bw)), int(max(0, d.y0 - pad * bh))
    x1, y1 = int(min(w, d.x1 + pad * bw + 1)), int(min(h, d.y1 + pad * bh + 1))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    return img[y0:y1, x0:x1]


class ClipEncoder:
    name = "clip-vit-b32"

    def __init__(self, compute_units: str = "all", cache_dir: Path | None = None):
        from vcstudio.ai.onnx_engine import create_session
        from vcstudio.ai.open_vocab import OWL_TOKENIZER, ClipTokenizer
        from vcstudio.ai.open_vocab import models_dir as ov_dir
        if not available():
            raise FileNotFoundError(f"CLIP model missing in {models_dir()} (scripts/fetch_models.py --clip)")
        d = models_dir()
        self.vision, self.backend = create_session(d / VISION, compute_units, cache_dir)
        self.text, _ = create_session(d / TEXT, compute_units, cache_dir)
        self.tok = ClipTokenizer(ov_dir() / OWL_TOKENIZER, TOKENS)
        self._text_cache: dict[str, np.ndarray] = {}

    def embed_images(self, images: list[np.ndarray], batch: int = 16) -> np.ndarray:
        if not images:
            return np.zeros((0, 512), np.float32)
        out = []
        for i in range(0, len(images), batch):
            x = np.stack([preprocess(im) for im in images[i:i + batch]])
            out.append(self.vision.run(["image_embeds"], {"pixel_values": x})[0])
        return _unit(np.concatenate(out).astype(np.float32))

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        todo = [t for t in dict.fromkeys(texts) if t not in self._text_cache]
        if todo:
            ids = []
            for t in todo:
                tok, mask = self.tok.encode(t)
                n = sum(mask)
                ids.append(tok[:n] + [self.tok.eos] * (TOKENS - n))
            emb = _unit(self.text.run(["text_embeds"], {"input_ids": np.array(ids, np.int64)})[0].astype(np.float32))
            self._text_cache.update(zip(todo, emb))
        return np.stack([self._text_cache[t] for t in texts]) if texts else np.zeros((0, 512), np.float32)


class ClipVerifier:
    """verify(img, dets) keeps the detections CLIP agrees with. A box survives when its own query has at least
    `min_prob` of CLIP's softmax over all queries plus the negative prompts (the queries compete with each other, so
    a "cup" box that looks like a "bottle" is relabelled when both are queried)."""

    def __init__(self, encoder: ClipEncoder, min_prob: float = 0.3, relabel: bool = True):
        self.enc, self.min_prob, self.relabel = encoder, min_prob, relabel
        self.checked = self.rejected = 0
        self.ms = 0.0

    def probabilities(self, img: np.ndarray, dets: list[Detection], queries: list[str]) -> np.ndarray:
        """(len(dets), len(queries)) CLIP softmax over the queries + negative prompts (negatives dropped from the
        result, so a row sums to < 1 when the crop looks like none of the queries); NaN rows for boxes that are not
        a query's or too small to crop."""
        out = np.full((len(dets), len(queries)), np.nan, np.float32)
        idx = [i for i, d in enumerate(dets) if d.label in queries]
        pairs = [(i, c) for i in idx if (c := crop(img, dets[i])) is not None]
        if pairs:
            texts = [TEMPLATE.format(q) for q in queries] + list(NEGATIVES)
            sim = self.enc.embed_images([c for _, c in pairs]) @ self.enc.embed_texts(texts).T
            p = np.exp(LOGIT_SCALE * (sim - sim.max(1, keepdims=True)))
            p /= p.sum(1, keepdims=True)
            out[[i for i, _ in pairs]] = p[:, :len(queries)]
        return out

    def verify(self, img: np.ndarray, dets: list[Detection], queries: list[str]) -> list[Detection]:
        t0 = time.perf_counter()
        probs = self.probabilities(img, dets, queries)
        keep = []
        for d, row in zip(dets, probs):
            if np.isnan(row).all():
                keep.append(d)                                          # not ours to judge (other sources, tiny)
                continue
            own_i = queries.index(d.label)
            best = int(row.argmax())
            self.checked += 1
            if row[own_i] >= self.min_prob:
                keep.append(d)
            elif self.relabel and best != own_i and row[best] >= self.min_prob:
                keep.append(Detection(queries[best], d.confidence, d.x0, d.y0, d.x1, d.y1, best,
                                      f"{d.source}+clip", mask=d.mask))
            else:
                self.rejected += 1
        self.ms = (time.perf_counter() - t0) * 1000
        return keep


class ImageQuery:
    """Objects that look like example pictures. add(name, images) stores the mean embedding of the examples;
    match(img, candidates) returns the candidates whose crop is at least `threshold` cosine-similar to the best
    matching query, labelled with its name (class id = position in names)."""

    def __init__(self, encoder: ClipEncoder, threshold: float = 0.75):
        self.enc, self.threshold = encoder, threshold
        self.names: list[str] = []
        self._emb: np.ndarray | None = None

    def add(self, name: str, images: list[np.ndarray]) -> None:
        if not images:
            raise ValueError("at least one example picture is needed")
        e = _unit(self.enc.embed_images(images).mean(0, keepdims=True))
        if name in self.names:
            self._emb[self.names.index(name)] = e[0]          # type: ignore[index]
        else:
            self.names.append(name)
            self._emb = e if self._emb is None else np.concatenate([self._emb, e])

    def match(self, img: np.ndarray, candidates: list[Detection]) -> list[Detection]:
        if self._emb is None or not candidates:
            return []
        crops = [(d, crop(img, d, pad=0.0)) for d in candidates]
        crops = [(d, c) for d, c in crops if c is not None]
        if not crops:
            return []
        sim = self.enc.embed_images([c for _, c in crops]) @ self._emb.T            # (candidates, queries)
        out = []
        for (d, _), row in zip(crops, sim):
            k = int(row.argmax())
            if row[k] >= self.threshold:
                out.append(Detection(self.names[k], float(row[k]), d.x0, d.y0, d.x1, d.y1, k, "owlv2+clip-image",
                                     mask=d.mask))
        return out


def load_example(path: str | Path) -> np.ndarray:
    """An example picture from disk (any format OpenCV reads, also with non-ASCII paths), BGR."""
    data = np.fromfile(str(path), np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None
    if img is None:
        raise ValueError(f"{Path(path).name}: not a picture OpenCV can read")
    return img
