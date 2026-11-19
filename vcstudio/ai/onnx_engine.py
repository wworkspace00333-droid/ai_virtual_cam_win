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


def _cache_file(path: Path, cache_dir: Path, provider: str, opts: dict) -> Path:
    """Optimised-model cache entry. ORT_ENABLE_ALL graphs can contain provider- and machine-specific nodes, so the
    key covers the model file (size, mtime), ONNX Runtime version, provider, its options and the GPU name."""
    import hashlib

    import onnxruntime as ort
    st = path.stat()
    gpu = _best_dml_device()[1] if provider == "DmlExecutionProvider" else ""
    key = f"{path.resolve()}|{st.st_size}|{int(st.st_mtime)}|{ort.__version__}|{provider}|{sorted(opts.items())}|{gpu}"
    return cache_dir / f"{path.stem}-{provider.replace('ExecutionProvider', '').lower()}-" \
                       f"{hashlib.sha256(key.encode()).hexdigest()[:12]}.opt.onnx"


def _remove_quietly(p: Path) -> None:
    """Delete a half-written cache file; a failure here (locked file, odd entry) must not stop the model loading."""
    try:
        p.unlink(missing_ok=True)
    except OSError as e:
        log.debug("could not remove %s: %s", p, e)


def create_session(path: Path, compute_units: str = "all", cache_dir: Path | None = None):
    """InferenceSession on the first provider of the plan that works; returns (session, provider label).

    With `cache_dir`, the graph optimised for that provider is saved on the first load and later loads open the saved
    graph with optimisation off (no repeated optimisation pass). A cache entry that fails to load is removed and the
    original model is used."""
    import onnxruntime as ort
    errors = []
    for name, opts in provider_plan(compute_units):
        def options(optimise: bool, save_to: Path | None = None, name: str = name):
            so = ort.SessionOptions()
            so.graph_optimization_level = (ort.GraphOptimizationLevel.ORT_ENABLE_ALL if optimise
                                           else ort.GraphOptimizationLevel.ORT_DISABLE_ALL)
            so.log_severity_level = 3
            so.intra_op_num_threads = cpu_threads()
            if name == "DmlExecutionProvider":        # DirectML requirements
                so.enable_mem_pattern = False
                so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            if save_to is not None:
                so.optimized_model_filepath = str(save_to)
            return so
        providers = [(name, opts)] if name == "CPUExecutionProvider" else [(name, opts), "CPUExecutionProvider"]
        cached = _cache_file(path, cache_dir, name, opts) if cache_dir is not None else None
        sess = None
        if cached is not None and cached.exists():
            try:
                sess = ort.InferenceSession(str(cached), sess_options=options(False), providers=providers)
            except Exception as e:                   # stale / foreign cache entry: rebuild it
                log.warning("Optimised-model cache %s unusable (%s); rebuilding", cached.name, e)
                cached.unlink(missing_ok=True)
                sess = None
        if sess is None:
            save = None
            if cached is not None:
                cached.parent.mkdir(parents=True, exist_ok=True)
                save = cached.with_suffix(".part")
            try:
                sess = ort.InferenceSession(str(path), sess_options=options(True, save), providers=providers)
            except Exception as e:                   # e.g. CUDA libraries missing, no D3D12 device
                if save is not None:
                    _remove_quietly(save)
                    # Writing the optimised copy can be what failed (disk full, read-only cache folder: ONNX
                    # Runtime then reports "Protobuf serialization failed"). The model itself may be fine: load it
                    # once more without the cache before giving up on this provider.
                    try:
                        sess = ort.InferenceSession(str(path), sess_options=options(True), providers=providers)
                        log.warning("Optimised-model cache for %s could not be written (%s); loaded without it",
                                    path.name, e)
                        save = None
                    except Exception as e2:
                        e = e2
                if sess is None:
                    errors.append(f"{name}: {e}")
                    log.warning("ONNX Runtime %s unavailable for %s: %s", name, path.name, e)
                    continue
            if save is not None and save.is_file() and sess.get_providers()[0] == name:
                try:
                    save.replace(cached)             # only a graph built for the provider actually used
                except OSError as e:                 # the cache is an optimisation: never fail the load for it
                    log.warning("Optimised-model cache %s not saved: %s", cached.name, e)
                    _remove_quietly(save)
            elif save is not None:
                _remove_quietly(save)
        used = sess.get_providers()[0]
        names = {"CUDAExecutionProvider": "CUDA", "DmlExecutionProvider": "DirectML", "CPUExecutionProvider": "CPU"}
        label = names.get(used, used)
        if used == "DmlExecutionProvider":
            _, gname = _best_dml_device()
            label += f" ({gname})" if gname else ""
        elif used != name:
            # A GPU provider was requested and the session was created, but ONNX Runtime put the graph on another
            # provider without an error (seen with DirectML on a PC without a usable adapter). Say so instead of
            # letting "GPU" settings run on the CPU unnoticed.
            label += f" ({names.get(name, name)} unavailable)"
            log.warning("ONNX Runtime: %s requested for %s, but the session runs on %s", name, path.name, used)
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


class OnnxYoloxDetector(_OnnxModel):
    """YOLOX (Megvii, Apache-2.0 code and weights) exported with its decoder outside the graph.

    Input: BGR, 0..255 float (no normalisation, the 0.1.1rc0+ exports), resized with the aspect ratio kept and
    padded with 114 at the right / bottom (YOLOX's own pre-processing, not the centred Ultralytics letterbox).
    Output (1, anchors, 5 + nc): per anchor of the 8 / 16 / 32 grids cx, cy (cell offsets), log w, log h,
    objectness, class scores; decoded here: xy = (o + grid) * stride, wh = exp(o) * stride, score = obj * cls."""
    max_det = 300
    STRIDES = (8, 16, 32)

    def __init__(self, loaded):
        super().__init__(loaded)
        grids, strides = [], []
        for s in self.STRIDES:
            n = self.size // s
            yv, xv = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
            grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
            strides.append(np.full((n * n, 1), s))
        self._grid = np.concatenate(grids).astype(np.float32)
        self._stride = np.concatenate(strides).astype(np.float32)

    def _blob(self, img_bgra: np.ndarray):
        h, w = img_bgra.shape[:2]
        r = min(self.size / w, self.size / h)
        nw, nh = int(w * r), int(h * r)
        c = self._canvas
        c[...] = PAD_VALUE
        c[:nh, :nw] = cv2.resize(img_bgra[..., :3], (nw, nh), interpolation=cv2.INTER_LINEAR)
        return cv2.dnn.blobFromImage(c), r          # NCHW float32, BGR, 0..255

    def detect(self, frame, conf: float = 0.25, iou: float = 0.5) -> tuple[list[Detection], float]:
        t0 = time.perf_counter()
        with frame.bgra() as img:
            blob, r = self._blob(img)
        if self.half:
            blob = blob.astype(np.float16)
        out = np.asarray(self.sess.run(None, {self.input_name: blob})[0][0], dtype=np.float32)
        if out.shape[0] != len(self._grid):
            raise ValueError(f"{self.name}: {out.shape[0]} anchors, expected {len(self._grid)} for {self.size} px input")
        cs = out[:, 5:] * out[:, 4:5]
        cls = cs.argmax(1)
        score = cs[np.arange(len(cs)), cls]
        m = score >= conf
        if not m.any():
            return [], (time.perf_counter() - t0) * 1000
        xy = (out[m, :2] + self._grid[m]) * self._stride[m]
        wh = np.exp(np.minimum(out[m, 2:4], 20.0)) * self._stride[m]
        boxes = np.concatenate((xy - wh / 2, xy + wh / 2), 1)
        scores, cls = score[m], cls[m]
        keep = nms(boxes + cls[:, None] * 4096.0, scores, iou)[: self.max_det]
        W, H = frame.width, frame.height
        index = {n: i for i, n in enumerate(self.class_names)}
        dets = []
        for i in keep:
            b = boxes[i] / r
            x0, y0 = float(np.clip(b[0], 0, W)), float(np.clip(b[1], 0, H))
            x1, y1 = float(np.clip(b[2], 0, W)), float(np.clip(b[3], 0, H))
            if x1 - x0 < 1 or y1 - y0 < 1:
                continue
            label = self._label(int(cls[i]))
            dets.append(Detection(label, float(scores[i]), x0, y0, x1, y1, index.get(label, int(cls[i])), "yolox"))
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
    """OpenCV Zoo YuNet face rectangles (MIT). Detection only: no identity recognition.

    Faces are found on a copy scaled to FACE_INPUT_MAX, where a face below ~10 px (about 30 px in a 1080p frame: a
    person a few metres away) is missed. Given the detector's people, the head area of each person too small for that
    pass is searched again on its own, enlarged crop ("Hide face" must not leave a distant face uncovered)."""
    name = "yunet"
    HEAD_CROPS = 6                # at most this many people re-checked per frame, largest first
    HEAD_INPUT = 160              # long side of an enlarged head crop
    SMALL_PERSON = 0.35           # a person shorter than this share of the frame height gets a head crop

    def __init__(self, score_threshold: float = 0.6, nms_threshold: float = 0.3):
        p = yunet_path()
        if not p.exists():
            raise FileNotFoundError(f"face model missing: {p}")
        self._det = cv2.FaceDetectorYN.create(str(p), "", (320, 320), score_threshold, nms_threshold, 50)
        self._size = (0, 0)
        self.head_crops = 0       # crops searched in the last frame (stats / tests)

    def _run(self, img: np.ndarray) -> np.ndarray:
        sh, sw = img.shape[:2]
        if self._size != (sw, sh):
            self._det.setInputSize((sw, sh))
            self._size = (sw, sh)
        _, faces = self._det.detect(img)
        return faces if faces is not None else np.zeros((0, 15), np.float32)

    def _head_boxes(self, people, W: int, H: int) -> list[tuple[int, int, int, int]]:
        small = [d for d in people if d.h < self.SMALL_PERSON * H and d.w >= 4 and d.h >= 8]
        small.sort(key=lambda d: -d.h)
        out = []
        for d in small[: self.HEAD_CROPS]:
            side = max(d.w, d.h * 0.4) * 1.2                 # head and shoulders: the top of the box, a bit wider
            cx, top = (d.x0 + d.x1) / 2, d.y0 - d.h * 0.06
            x0, y0 = max(0, int(cx - side / 2)), max(0, int(top))
            x1, y1 = min(W, int(np.ceil(cx + side / 2))), min(H, int(np.ceil(top + side)))
            if x1 - x0 >= 4 and y1 - y0 >= 4:
                out.append((x0, y0, x1, y1))
        return out

    def detect(self, frame, people=()) -> tuple[list[Detection], float]:
        t0 = time.perf_counter()
        W, H = frame.width, frame.height
        boxes: list[tuple] = []                     # x0, y0, x1, y1, score, (5, 2) landmarks
        with frame.bgra() as img:
            h, w = img.shape[:2]
            s = min(1.0, FACE_INPUT_MAX / max(w, h))
            small = cv2.resize(img[..., :3], (max(1, int(w * s)), max(1, int(h * s))),
                               interpolation=cv2.INTER_AREA) if s < 1 else np.ascontiguousarray(img[..., :3])
            for f in self._run(small):
                boxes.append((f[0] / s, f[1] / s, (f[0] + f[2]) / s, (f[1] + f[3]) / s, float(f[14]),
                              f[4:14].reshape(5, 2) / s))
            found = list(boxes)
            heads = [hb for hb in (self._head_boxes(people, w, h) if s < 1 else [])
                     if not any(hb[0] <= (b[0] + b[2]) / 2 <= hb[2] and hb[1] <= (b[1] + b[3]) / 2 <= hb[3]
                                for b in found)]       # a face already found there: no second look
            self.head_crops = len(heads)
            for x0, y0, x1, y1 in heads:
                k = self.HEAD_INPUT / max(x1 - x0, y1 - y0)
                crop = cv2.resize(img[y0:y1, x0:x1, :3], (max(1, int((x1 - x0) * k)), max(1, int((y1 - y0) * k))),
                                  interpolation=cv2.INTER_LINEAR if k > 1 else cv2.INTER_AREA)
                for f in self._run(crop):
                    boxes.append((x0 + f[0] / k, y0 + f[1] / k, x0 + (f[0] + f[2]) / k, y0 + (f[1] + f[3]) / k,
                                  float(f[14]), f[4:14].reshape(5, 2) / k + np.array([x0, y0], np.float32)))
        out: list[Detection] = []
        for bx0, by0, bx1, by1, score, pts in sorted(boxes, key=lambda b: -b[4]):
            x0, y0, x1, y1 = max(0.0, bx0), max(0.0, by0), min(float(W), bx1), min(float(H), by1)
            if x1 <= x0 or y1 <= y0:
                continue
            if any(_iou((x0, y0, x1, y1), (o.x0, o.y0, o.x1, o.y1)) > 0.3 for o in out):
                continue                                    # the same face from the frame pass and a head crop
            out.append(Detection(FACE, score, x0, y0, x1, y1, -1, "yunet_face",
                                 points=np.asarray(pts, np.float32)))
        return out, (time.perf_counter() - t0) * 1000


def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0
