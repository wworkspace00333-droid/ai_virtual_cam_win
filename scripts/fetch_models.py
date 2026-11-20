"""Download the built-in detection models into models/ and verify their SHA-256 (developer / build tool).

    python scripts/fetch_models.py [--dir models] [--check]

The installer ships the built-in models; the app downloads nothing on its own. The one exception is the optional
"any object by text" set, which the app fetches only when the user clicks "Download models"
(vcstudio/ai/model_download.py). Other models (for example Ultralytics YOLO, AGPL-3.0) can only be added by the
user through "Import model". The weights are not kept in
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


# optional, --open-vocab: "any object" by text (OWLv2, Google, Apache-2.0) and promptable masks (SAM 2.1, Meta,
# Apache-2.0); 8-bit ONNX exports pinned to a commit, ~220 MB, not in the installer: the app downloads them on the
# user's click (vcstudio/ai/model_download.py, the same table).
sys.path.insert(0, str(ROOT))
from vcstudio.ai.model_download import CLIP as _CLIP  # noqa: E402
from vcstudio.ai.model_download import HANDS as _HANDS  # noqa: E402
from vcstudio.ai.model_download import OPEN_VOCAB as _OV  # noqa: E402
from vcstudio.ai.model_download import POSE as _POSE  # noqa: E402
from vcstudio.ai.model_download import TEXT as _TEXT  # noqa: E402
from vcstudio.ai.model_download import TEXT_REC as _TEXT_REC  # noqa: E402
from vcstudio.ai.model_download import DEPTH as _DEPTH  # noqa: E402
from vcstudio.ai.model_download import FACE_MESH as _FACE_MESH  # noqa: E402

OPEN_VOCAB = {name: (url, digest, lic) for name, (url, digest, lic, _size) in _OV.items()}
# optional, --hands: gesture control (MediaPipe palm + hand landmarks, OpenCV Zoo ONNX, Apache-2.0, ~8 MB)
HANDS = {name: (url, digest, lic) for name, (url, digest, lic, _size) in _HANDS.items()}
# optional, --clip: CLIP ViT-B/32 (OpenAI, MIT, Xenova 8-bit ONNX, ~147 MB) for checking OWLv2 finds and image queries
CLIP = {name: (url, digest, lic) for name, (url, digest, lic, _size) in _CLIP.items()}
# optional, --pose / --text (0.6.0): MediaPipe person + pose (~17 MB), PP-OCRv3 text detection (~2.4 MB), Apache-2.0
POSE = {name: (url, digest, lic) for name, (url, digest, lic, _size) in _POSE.items()}
TEXT = {name: (url, digest, lic) for name, (url, digest, lic, _size) in {**_TEXT, **_TEXT_REC}.items()}
# optional, --depth (27 MB) / --mesh (5 MB): depth background source, dense face mesh
DEPTH = {name: (url, digest, lic) for name, (url, digest, lic, _size) in _DEPTH.items()}
MESH = {name: (url, digest, lic) for name, (url, digest, lic, _size) in _FACE_MESH.items()}


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
    ap.add_argument("--hands", action="store_true", help="also the optional hand models for gestures (~8 MB)")
    ap.add_argument("--clip", action="store_true", help="also CLIP ViT-B/32 for check + image query (~147 MB)")
    ap.add_argument("--pose", action="store_true", help="also body pose models for AI Vision (~17 MB)")
    ap.add_argument("--text", action="store_true", help="also the text detector and recogniser for the privacy shield (~19 MB)")
    ap.add_argument("--depth", action="store_true", help="also Depth Anything V2 Small for depth backgrounds (~27 MB)")
    ap.add_argument("--mesh", action="store_true", help="also the dense face mesh model (~5 MB)")
    a = ap.parse_args()
    bad = 0
    todo = dict(MODELS, **(OPEN_VOCAB if a.open_vocab else {}), **(HANDS if a.hands else {}), **(CLIP if a.clip else {}),
                **(POSE if a.pose else {}), **(TEXT if a.text else {}), **(DEPTH if a.depth else {}),
                **(MESH if a.mesh else {}))
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
