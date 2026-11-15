"""ONNX Runtime inference: YOLO detection, YOLO-seg segmentation, YuNet faces (the Windows AI backend).

Same interfaces as the Core ML classes (coreml_engine.py, segmentation.py): detect(frame, conf, iou)
returns (list[Detection], milliseconds), so the AI engine, tracker and compositor are unchanged.

Execution providers ("compute units" in profiles keep their keys for compatibility):
  all          CUDA if installed (onnxruntime-gpu) -> DirectML on the best hardware GPU -> CPU
  cpu_and_gpu  same order, and DirectML is tried even without a recognised hardware GPU
  cpu_and_ne   = all (there is no Neural Engine on Windows)
  cpu_only     CPU
A provider that fails to create the session falls back to the next one; the provider actually used is
reported (AI Engine page) instead of the requested one.

Pre-processing matches Ultralytics: letterbox to the model size with grey (114) padding, BGR -> RGB,
0..1 float, NCHW. Detection outputs supported: raw (1, 4 + classes, anchors) with NMS here (class-aware,
same NMS as segmentation), or end-to-end (1, N, 6) exported with nms=True.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import cv2
import numpy as np

from vcstudio.ai.detection import Detection
from vcstudio.ai.labels import FACE
from vcstudio.ai.segmentation import PAD_VALUE, SegmentationDetector, nms

log = logging.getLogger(__name__)


def _best_dml_device() -> tuple[int | None, str]:
    """(DXGI adapter index, name) of the hardware GPU with the most dedicated memory, or (None, "")."""
    try:
        from vcstudio.platform.win.sysinfo import hardware_gpus
        gpus = hardware_gpus()
    except Exception:
        return None, ""
    if not gpus:
        return None, ""
    g = max(gpus, key=lambda a: a["dedicated_vram_mb"])
    return g["index"], g["name"]


def cpu_threads() -> int:
    """Inference threads: half the physical cores (1..8). ONNX Runtime's default (all cores) starved the
    capture and output threads on CPU-only machines (60 fps input dropped to 47 fps in tests); by design
    AI never delays output, so it gets a share of the CPU, not all of it."""
    import psutil
    cores = psutil.cpu_count(logical=False) or psutil.cpu_count() or 2
    return max(1, min(8, cores // 2))


def provider_plan(compute_units: str) -> list[tuple[str, dict]]:
    """Execution providers to try, in order, for a compute-units setting."""
    import onnxruntime as ort
    avail = set(ort.get_available_providers())
    plan: list[tuple[str, dict]] = []
    if compute_units != "cpu_only":
        if "CUDAExecutionProvider" in avail:
            plan.append(("CUDAExecutionProvider", {}))
        if "DmlExecutionProvider" in avail:
            idx, _ = _best_dml_device()
            if idx is not None:
                plan.append(("DmlExecutionProvider", {"device_id": idx}))
            elif compute_units == "cpu_and_gpu":
                plan.append(("DmlExecutionProvider", {"device_id": 0}))
    plan.append(("CPUExecutionProvider", {}))
    return plan


def create_session(path: Path, compute_units: str = "all"):
    """InferenceSession on the first provider of the plan that works; returns (session, provider label)."""
    import onnxruntime as ort
    errors = []
    for name, opts in provider_plan(compute_units):
        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        so.log_severity_level = 3
        so.intra_op_num_threads = cpu_threads()
        if name == "DmlExecutionProvider":            # DirectML requirements
            so.enable_mem_pattern = False
            so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        providers = [(name, opts)] if name == "CPUExecutionProvider" else [(name, opts), "CPUExecutionProvider"]
        try:
            sess = ort.InferenceSession(str(path), sess_options=so, providers=providers)
        except Exception as e:                       # e.g. CUDA libraries missing, no D3D12 device
            errors.append(f"{name}: {e}")
            log.warning("ONNX Runtime %s unavailable for %s: %s", name, path.name, e)
            continue
        used = sess.get_providers()[0]
        label = {"CUDAExecutionProvider": "CUDA", "DmlExecutionProvider": "DirectML",
                 "CPUExecutionProvider": "CPU"}.get(used, used)
        if used == "DmlExecutionProvider":
            _, gname = _best_dml_device()
            label += f" ({gname})" if gname else ""
        return sess, label
    raise RuntimeError("no ONNX Runtime execution provider could load the model: " + "; ".join(errors))


def letterbox_blob(img_bgra: np.ndarray, size: int, canvas: np.ndarray | None = None):
    """Ultralytics letterbox -> (NCHW float32 RGB 0..1 blob, scale, pad_x, pad_y)."""
    h, w = img_bgra.shape[:2]
    r = min(size / w, size / h)
    nw, nh = int(round(w * r)), int(round(h * r))
    px, py = (size - nw) // 2, (size - nh) // 2
    if canvas is None or canvas.shape[:2] != (size, size):
        canvas = np.empty((size, size, 3), np.uint8)
    canvas[...] = PAD_VALUE
    canvas[py:py + nh, px:px + nw] = cv2.resize(img_bgra[..., :3], (nw, nh), interpolation=cv2.INTER_LINEAR)
    blob = cv2.dnn.blobFromImage(canvas, scalefactor=1 / 255.0, swapRB=True)
    return blob, r, px, py


class _OnnxModel:
    def __init__(self, loaded):
        self.loaded = loaded
        self.name = loaded.info.name
        self.class_names = loaded.info.classes
        self._labels = loaded.info.label_map()
        self.sess = loaded.mlmodel
        inp = self.sess.get_inputs()[0]
        self.input_name = inp.name
        self.half = inp.type == "tensor(float16)"
        shape = inp.shape
        self.size = int(shape[2]) if isinstance(shape[2], int) else int(loaded.info.imgsz)
        self._canvas = np.empty((self.size, self.size, 3), np.uint8)

    def _run(self, frame):
        with frame.bgra() as img:
            blob, r, px, py = letterbox_blob(img, self.size, self._canvas)
        if self.half:
            blob = blob.astype(np.float16)
        outs = self.sess.run(None, {self.input_name: blob})
        return [np.asarray(o, dtype=np.float32) for o in outs], r, px, py

    def _label(self, cls: int) -> str:
        name = self.class_names[cls] if 0 <= cls < len(self.class_names) else str(cls)
        return self._labels.get(name, name)


class OnnxDetector(_OnnxModel):
    max_det = 300

    def detect(self, frame, conf: float = 0.25, iou: float = 0.5) -> tuple[list[Detection], float]:
        t0 = time.perf_counter()
        outs, r, px, py = self._run(frame)
        out = outs[0][0]
        W, H = frame.width, frame.height
        if out.ndim == 2 and out.shape[1] == 6 and out.shape[0] != 6:     # end-to-end (N, 6) x0 y0 x1 y1 score cls
            rows = out[out[:, 4] >= conf]
            boxes, scores, cls = rows[:, :4], rows[:, 4], rows[:, 5].astype(int)
            keep = list(range(len(rows)))
        else:                                                              # raw (4 + nc, anchors)
            nc = out.shape[0] - 4
            cs = out[4:4 + nc]
            score = cs.max(0)
            m = score >= conf
            if not m.any():
                return [], (time.perf_counter() - t0) * 1000
            cls = cs[:, m].argmax(0)
            xywh = out[:4, m].T
            scores = score[m]
            boxes = np.empty_like(xywh)
            boxes[:, 0] = xywh[:, 0] - xywh[:, 2] / 2; boxes[:, 1] = xywh[:, 1] - xywh[:, 3] / 2
            boxes[:, 2] = xywh[:, 0] + xywh[:, 2] / 2; boxes[:, 3] = xywh[:, 1] + xywh[:, 3] / 2
            keep = nms(boxes + cls[:, None] * 4096.0, scores, iou)[: self.max_det]
        dets = []
        index = {n: i for i, n in enumerate(self.class_names)}
        for i in keep:
            b = boxes[i]
            x0 = float(np.clip((b[0] - px) / r, 0, W)); y0 = float(np.clip((b[1] - py) / r, 0, H))
            x1 = float(np.clip((b[2] - px) / r, 0, W)); y1 = float(np.clip((b[3] - py) / r, 0, H))
            if x1 - x0 < 1 or y1 - y0 < 1:
                continue
            label = self._label(int(cls[i]))
            dets.append(Detection(label, float(scores[i]), x0, y0, x1, y1, index.get(label, int(cls[i])), "yolo"))
        return dets, (time.perf_counter() - t0) * 1000


class OnnxSegmentationDetector(_OnnxModel):
    """YOLO-seg: outputs (1, 4 + nc + nm, N) and (1, nm, mh, mw); post-processing shared with Core ML."""
    max_det = SegmentationDetector.max_det
    postprocess = SegmentationDetector.postprocess

    def detect(self, frame, conf: float = 0.25, iou: float = 0.5) -> tuple[list[Detection], float]:
        t0 = time.perf_counter()
        outs, r, px, py = self._run(frame)
        pred = next(a for a in outs if a.ndim == 3)[0]
        protos = next(a for a in outs if a.ndim == 4)[0]
        dets = self.postprocess(pred, protos, conf, iou, r, px, py, frame.width, frame.height)
        if self._labels:
            dets = [Detection(self._labels.get(d.label, d.label), d.confidence, d.x0, d.y0, d.x1, d.y1, d.class_id,
                              d.source, mask=d.mask) for d in dets]
        return dets, (time.perf_counter() - t0) * 1000


# ------------------------------------------------------------------------------------------- faces
YUNET_FILE = "face_detection_yunet_2023mar.onnx"
FACE_INPUT_MAX = 640          # faces are found on a downscaled copy (YuNet is fast at this size)


def yunet_path() -> Path:
    from vcstudio.core.resources import models_dir
    return models_dir() / YUNET_FILE


class YuNetFaceDetector:
    """OpenCV Zoo YuNet face rectangles (MIT). Detection only: no identity recognition."""
    name = "yunet"

    def __init__(self, score_threshold: float = 0.6, nms_threshold: float = 0.3):
        p = yunet_path()
        if not p.exists():
            raise FileNotFoundError(f"face model missing: {p}")
        self._det = cv2.FaceDetectorYN.create(str(p), "", (320, 320), score_threshold, nms_threshold, 50)
        self._size = (0, 0)

    def detect(self, frame) -> tuple[list[Detection], float]:
        t0 = time.perf_counter()
        with frame.bgra() as img:
            h, w = img.shape[:2]
            s = min(1.0, FACE_INPUT_MAX / max(w, h))
            small = cv2.resize(img[..., :3], (max(1, int(w * s)), max(1, int(h * s))),
                               interpolation=cv2.INTER_AREA) if s < 1 else np.ascontiguousarray(img[..., :3])
        sh, sw = small.shape[:2]
        if self._size != (sw, sh):
            self._det.setInputSize((sw, sh))
            self._size = (sw, sh)
        _, faces = self._det.detect(small)
        out = []
        for f in (faces if faces is not None else []):
            x, y, bw, bh, score = float(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[14])
            x0, y0 = max(0.0, x / s), max(0.0, y / s)
            x1, y1 = min(float(frame.width), (x + bw) / s), min(float(frame.height), (y + bh) / s)
            if x1 > x0 and y1 > y0:
                out.append(Detection(FACE, score, x0, y0, x1, y1, -1, "yunet_face"))
        return out, (time.perf_counter() - t0) * 1000
