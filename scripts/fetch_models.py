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


HF = "https://huggingface.co/"
OWL_REV = "Xenova/owlv2-base-patch16-ensemble/resolve/3e255bc9bf304b3358a8c1945c0a9514eefe7f10/"
SAM_REV = "onnx-community/sam2.1-hiera-tiny-ONNX/resolve/814a066640debee5a91e70aa401fb8e17e030503/"
# optional, --open-vocab: "any object" by text (OWLv2, Google, Apache-2.0) and promptable masks (SAM 2.1, Meta,
# Apache-2.0); 8-bit ONNX exports pinned to a commit. ~220 MB, not shipped in the installer yet.
OPEN_VOCAB = {
    "owlv2_base_ensemble_int8.onnx": (HF + OWL_REV + "onnx/model_quantized.onnx",
                                      "af8d6a041e4114b06eafabb03047350c7b8f136670a3f8afc7e73c5a74c2b24b", "Apache-2.0"),
    "owlv2_tokenizer.json": (HF + OWL_REV + "tokenizer.json",
                             "bbce3185c27ac4daf41ea2dfc864785bdcd1a42815e7646324af20edd671a578", "Apache-2.0"),
    "sam2.1_tiny/vision_encoder_int8.onnx": (HF + SAM_REV + "onnx/vision_encoder_int8.onnx",
                                             "d848e08b747ba44ee59b31246988528d5ecc67d1e1c0a2f5c08395c86d92176f", "Apache-2.0"),
    "sam2.1_tiny/vision_encoder_int8.onnx_data": (HF + SAM_REV + "onnx/vision_encoder_int8.onnx_data",
                                                  "4359d45f8aafcbc69f945e714af7fb06b74b24c753b5af86b6a230ecd0e737bc",
                                                  "Apache-2.0"),
    "sam2.1_tiny/prompt_encoder_mask_decoder_int8.onnx": (
        HF + SAM_REV + "onnx/prompt_encoder_mask_decoder_int8.onnx",
        "e53727dc441a3c8716dd4a606d5bbf920e33e7545d1a6ff843c9fb804c7d19b6", "Apache-2.0"),
    "sam2.1_tiny/prompt_encoder_mask_decoder_int8.onnx_data": (
        HF + SAM_REV + "onnx/prompt_encoder_mask_decoder_int8.onnx_data",
        "fdb3179a32388267a944a416c0f992c77a138b69a4bfd94f0bbf7089430d7489", "Apache-2.0"),
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
    ap.add_argument("--open-vocab", action="store_true", help="also the optional OWLv2 + SAM 2.1 models (~220 MB)")
    a = ap.parse_args()
    bad = 0
    todo = dict(MODELS, **(OPEN_VOCAB if a.open_vocab else {}))
    for name, (url, digest, lic) in todo.items():
        dst = a.dir / name
        dst.parent.mkdir(parents=True, exist_ok=True)
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
