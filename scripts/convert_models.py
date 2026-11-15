"""Convert Ultralytics YOLO weights for the app: Core ML (macOS) or ONNX (Windows).

Run with the conversion environment (keeps PyTorch out of the runtime app):
    macOS:   .venv-convert/bin/python scripts/convert_models.py                   # yolo11n, yolo11s, yolo11n-seg
             .venv-convert/bin/python scripts/convert_models.py yolo11m yolo11m-seg
    Windows: C:\\vcs-convert\\Scripts\\python scripts\\convert_models.py --format onnx
             C:\\vcs-convert\\Scripts\\python scripts\\convert_models.py --format onnx yolo11m yolo11m-seg

Output: models/<name>.mlpackage or models/<name>.onnx, plus models/<name>.json (class names, input size,
provenance). ONNX models are exported raw (no NMS inside; NMS runs in vcstudio/ai/onnx_engine.py), FP32,
opset 17, static 640x640 input: DirectML and the CPU provider both run them.
With --format onnx the YuNet face detector (OpenCV Zoo, MIT) is fetched as well.
Weights are fetched by Ultralytics from its GitHub releases on first use.
License note: Ultralytics YOLO weights/code are AGPL-3.0 (or Ultralytics Enterprise license).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"
YUNET_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
             "face_detection_yunet_2023mar.onnx")
YUNET_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"


def convert(name: str, imgsz: int, work: Path, fmt: str) -> None:
    from ultralytics import YOLO
    import ultralytics

    work.mkdir(parents=True, exist_ok=True)
    pt = work / f"{name}.pt"
    model = YOLO(str(pt) if pt.exists() else f"{name}.pt")
    if not pt.exists() and Path(f"{name}.pt").exists():
        shutil.move(f"{name}.pt", pt)
    t0 = time.time()
    task = getattr(model, "task", "detect")
    if fmt == "coreml":
        # Detection: NMS inside the Core ML pipeline (Vision-compatible). Segmentation: raw outputs
        # (boxes+coeffs, prototypes); NMS and mask assembly run in vcstudio/ai/segmentation.py.
        out = Path(model.export(format="coreml", nms=(task == "detect"), half=True, imgsz=imgsz))
        dest = MODELS / f"{name}.mlpackage"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(out), dest)
        mfmt = "coreml-pipeline-nms" if task == "detect" else "coreml-raw-seg"
        precision = "fp16"
    else:
        out = Path(model.export(format="onnx", imgsz=imgsz, opset=17, simplify=True, dynamic=False, half=False,
                                nms=False))
        dest = MODELS / f"{name}.onnx"
        dest.unlink(missing_ok=True)
        shutil.move(str(out), dest)
        mfmt = "onnx-raw-detect" if task == "detect" else "onnx-raw-seg"
        precision = "fp32"
    names = model.names if isinstance(model.names, dict) else dict(enumerate(model.names))
    meta = {
        "name": name,
        "task": task,
        "format": mfmt,
        "imgsz": imgsz,
        "precision": precision,
        "classes": [names[i] for i in sorted(names)],
        "source": f"Ultralytics {ultralytics.__version__} {name}.pt",
        "license": "AGPL-3.0 (Ultralytics)",
        "converted": time.strftime("%Y-%m-%d %H:%M:%S"),
        "convert_seconds": round(time.time() - t0, 1),
    }
    (MODELS / f"{name}.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"OK {dest} ({len(meta['classes'])} classes)")


def fetch_yunet() -> None:
    dest = MODELS / "face_detection_yunet_2023mar.onnx"
    if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest() == YUNET_SHA256:
        print(f"OK {dest} (already present)")
        return
    data = urllib.request.urlopen(YUNET_URL, timeout=60).read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != YUNET_SHA256:
        raise SystemExit(f"YuNet download has an unexpected checksum {digest}; not installed")
    dest.write_bytes(data)
    print(f"OK {dest} (YuNet face detector, OpenCV Zoo, MIT)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="*", default=["yolo11n", "yolo11s", "yolo11n-seg"])
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--format", choices=["coreml", "onnx"], default="coreml")
    a = ap.parse_args()
    MODELS.mkdir(exist_ok=True)
    for m in a.models:
        convert(m, a.imgsz, ROOT / "build" / "weights", a.format)
    if a.format == "onnx":
        fetch_yunet()


if __name__ == "__main__":
    main()
