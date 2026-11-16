"""Download the built-in detection models into models/ and verify their SHA-256 (developer / build tool).

    python scripts/fetch_models.py [--dir models] [--check]

The app itself never downloads anything: the installer ships these files, and other models (for example
Ultralytics YOLO, AGPL-3.0) can only be added by the user through "Import model". The weights are not kept in
git; this script is the single place where their origin, licence and checksum are recorded (also in
THIRD_PARTY_NOTICES.md). --check only verifies files that are already there (exit 1 on a mismatch or a missing file).
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
YOLOX = "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/"

# file -> (url, sha256, licence)
MODELS = {
    "yolox_nano.onnx": (YOLOX + "yolox_nano.onnx",
                        "c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d", "Apache-2.0"),
    "yolox_tiny.onnx": (YOLOX + "yolox_tiny.onnx",
                        "427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7", "Apache-2.0"),
    "yolox_s.onnx": (YOLOX + "yolox_s.onnx",
                     "c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063", "Apache-2.0"),
    # person segmentation for background blur / replacement: PP-HumanSeg (PaddlePaddle), OpenCV Zoo ONNX port
    "human_segmentation_pphumanseg_2023mar.onnx": (
        "https://github.com/opencv/opencv_zoo/raw/main/models/human_segmentation_pphumanseg/"
        "human_segmentation_pphumanseg_2023mar.onnx",
        "552d8a984054e59b5d773d24b9b12022b22046ceb2bbc4c9aaeaceb36a9ddf24", "Apache-2.0"),
    # committed to git (227 KB); listed so --check covers it
    "face_detection_yunet_2023mar.onnx": (
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4", "MIT"),
}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=ROOT / "models")
    ap.add_argument("--check", action="store_true", help="verify only, download nothing")
    a = ap.parse_args()
    bad = 0
    for name, (url, digest, lic) in MODELS.items():
        dst = a.dir / name
        if dst.exists() and sha256(dst) == digest:
            print(f"ok       {name} ({lic})")
            continue
        if a.check:
            print(f"{'BAD' if dst.exists() else 'MISSING':8} {name}")
            bad += 1
            continue
        print(f"download {name} <- {url}")
        tmp = dst.with_suffix(".part")
        with urllib.request.urlopen(url, timeout=120) as r, tmp.open("wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
        if sha256(tmp) != digest:
            tmp.unlink()
            print(f"BAD      {name}: SHA-256 mismatch, file removed")
            bad += 1
            continue
        tmp.replace(dst)
        print(f"ok       {name} ({lic})")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
