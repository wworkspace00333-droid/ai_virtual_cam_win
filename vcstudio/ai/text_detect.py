"""Local text detection and (optional) recognition for the privacy shield (0.6.0 Phase 5). Nothing leaves the PC.

  TextDetector    PP-OCRv3 DB text detector (PaddlePaddle, Apache-2.0; ONNX from the OpenCV Zoo, 2.4 MB, downloaded
                  with SHA-256 check, vcstudio/ai/model_download.py TEXT) through OpenCV's TextDetectionModel_DB, which
                  does the reference pre- and post-processing (mean subtraction, binarisation 0.3, polygon score 0.5,
                  unclip 2.0). Returns rotated quadrilaterals and their axis-aligned boxes.
  TextRecognizer  CRNN (OpenCV Zoo, Apache-2.0) through TextRecognitionModel, CTC greedy decoding. The English model
                  reads 0-9 and a-z only (no punctuation): enough for phone numbers, card / account-like numbers and
                  keywords, not for e-mail addresses (no "@"). It is used only when its file is present (a separate
                  download, 16 MB).

Measured on the 2-core development server (CPU): detection at 736×736 ≈ 110 ms per frame, so the privacy shield runs
it on its own thread a couple of times per second and keeps the boxes between runs. Small text (under ~12 px high in
the frame) is often missed: the shield never promises that all text is found (see docs/PRO_060_ARCHITECTURE.md).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

DET_FILE = "text/text_detection_en_ppocrv3_2023may.onnx"
REC_FILE = "text/text_recognition_CRNN_EN_2021sep.onnx"
EN_CHARSET = "0123456789abcdefghijklmnopqrstuvwxyz"
MEAN = (122.67891434, 116.66876762, 104.00698793)


def models_dir() -> Path:
    from vcstudio.ai.open_vocab import user_models_dir
    from vcstudio.core.resources import models_dir as md
    bundled = md()
    return bundled if (bundled / DET_FILE).exists() else user_models_dir()


def available() -> bool:
    return (models_dir() / DET_FILE).exists()


def recognizer_available() -> bool:
    return (models_dir() / REC_FILE).exists()


@dataclass
class TextBox:
    quad: np.ndarray                              # (4, 2) int32 corners in frame pixels
    box: tuple[int, int, int, int]                # x0, y0, x1, y1
    score: float
    text: str = ""


class TextDetector:
    def __init__(self, side: int = 736, bin_thresh: float = 0.3, poly_thresh: float = 0.5):
        self.model = cv2.dnn.TextDetectionModel_DB(str(models_dir() / DET_FILE))
        self.model.setBinaryThreshold(bin_thresh)
        self.model.setPolygonThreshold(poly_thresh)
        self.model.setUnclipRatio(2.0)
        self.model.setMaxCandidates(200)
        self.side = side
        self._size: tuple[int, int] | None = None
        self.ms = 0.0

    def set_sensitivity(self, s: float) -> None:
        """0 (only clear text) .. 1 (also faint text, more false boxes): the polygon score threshold 0.7 .. 0.3."""
        self.model.setPolygonThreshold(float(0.7 - 0.4 * min(1.0, max(0.0, s))))

    def detect(self, bgr: np.ndarray) -> list[TextBox]:
        t0 = time.perf_counter()
        h, w = bgr.shape[:2]
        k = self.side / max(w, h)
        iw, ih = max(32, int(round(w * k / 32)) * 32), max(32, int(round(h * k / 32)) * 32)   # multiples of 32
        if self._size != (iw, ih):
            self.model.setInputParams(1.0 / 255.0, (iw, ih), MEAN, False)
            self._size = (iw, ih)
        quads, scores = self.model.detect(bgr)
        out = []
        for q, sc in zip(quads, scores if len(scores) else [1.0] * len(quads)):
            q = np.asarray(q, np.int32).reshape(4, 2)
            x0, y0 = q.min(0)
            x1, y1 = q.max(0)
            x0, y0, x1, y1 = max(0, int(x0)), max(0, int(y0)), min(w, int(x1)), min(h, int(y1))
            if x1 - x0 >= 4 and y1 - y0 >= 4:
                out.append(TextBox(q, (x0, y0, x1, y1), float(sc)))
        self.ms = (time.perf_counter() - t0) * 1000
        return out


class TextRecognizer:
    """CRNN over the detector's quadrilaterals (straightened to 100×32 grey)."""

    def __init__(self, charset: str = EN_CHARSET):
        self.model = cv2.dnn.TextRecognitionModel(str(models_dir() / REC_FILE))
        self.model.setDecodeType("CTC-greedy")
        self.model.setVocabulary(list(charset))
        self.model.setInputParams(1.0 / 127.5, (100, 32), (127.5, 127.5, 127.5))

    def read(self, bgr: np.ndarray, tb: TextBox) -> str:
        q = tb.quad.astype(np.float32)
        # order: the detector gives bottom-left, top-left, top-right, bottom-right
        s = q.sum(1)
        d = np.diff(q, axis=1).ravel()
        tl, br, tr, bl = q[np.argmin(s)], q[np.argmax(s)], q[np.argmin(d)], q[np.argmax(d)]
        M = cv2.getPerspectiveTransform(np.array([bl, tl, tr, br], np.float32),
                                        np.array([[0, 31], [0, 0], [99, 0], [99, 31]], np.float32))
        crop = cv2.warpPerspective(bgr, M, (100, 32))
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        return str(self.model.recognize(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)) or "")
