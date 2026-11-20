"""Optional model download (the app's only network access), started only by the user's click.

What: the "any object by text" models — OWLv2 base (Google, Apache-2.0) and SAM 2.1 tiny (Meta, Apache-2.0), 8-bit
ONNX exports pinned to a Hugging Face commit, about 220 MB. They are not in the installer to keep it small.
Where from: huggingface.co, the exact URLs below (no other host, nothing is sent but the HTTPS request itself).
Where to: <app data>\\models (%LOCALAPPDATA%\\AIVirtualCameraStudio\\models on Windows), next to imported models.
Checks: every file is written as .part, its SHA-256 compared with the value recorded here, and only then renamed;
a mismatch deletes the file. A cancelled download leaves no partial model behind.
Off switch: VCS_NO_NETWORK=1 (or settings "privacy.allow_downloads": false) hides the button and refuses downloads.

scripts/fetch_models.py --open-vocab imports the same table, so the developer tool and the app cannot drift apart.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import urllib.request
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

HF = "https://huggingface.co/"
OWL_REV = "Xenova/owlv2-base-patch16-ensemble/resolve/3e255bc9bf304b3358a8c1945c0a9514eefe7f10/"
SAM_REV = "onnx-community/sam2.1-hiera-tiny-ONNX/resolve/814a066640debee5a91e70aa401fb8e17e030503/"

# relative file -> (url, sha256, licence, size in bytes for the progress bar; 0 = unknown)
OPEN_VOCAB: dict[str, tuple[str, str, str, int]] = {
    "owlv2_base_ensemble_int8.onnx": (HF + OWL_REV + "onnx/model_quantized.onnx",
                                      "af8d6a041e4114b06eafabb03047350c7b8f136670a3f8afc7e73c5a74c2b24b", "Apache-2.0", 0),
    "owlv2_tokenizer.json": (HF + OWL_REV + "tokenizer.json",
                             "bbce3185c27ac4daf41ea2dfc864785bdcd1a42815e7646324af20edd671a578", "Apache-2.0", 0),
    "sam2.1_tiny/vision_encoder_int8.onnx": (HF + SAM_REV + "onnx/vision_encoder_int8.onnx",
                                             "d848e08b747ba44ee59b31246988528d5ecc67d1e1c0a2f5c08395c86d92176f",
                                             "Apache-2.0", 0),
    "sam2.1_tiny/vision_encoder_int8.onnx_data": (HF + SAM_REV + "onnx/vision_encoder_int8.onnx_data",
                                                  "4359d45f8aafcbc69f945e714af7fb06b74b24c753b5af86b6a230ecd0e737bc",
                                                  "Apache-2.0", 0),
    "sam2.1_tiny/prompt_encoder_mask_decoder_int8.onnx": (
        HF + SAM_REV + "onnx/prompt_encoder_mask_decoder_int8.onnx",
        "e53727dc441a3c8716dd4a606d5bbf920e33e7545d1a6ff843c9fb804c7d19b6", "Apache-2.0", 0),
    "sam2.1_tiny/prompt_encoder_mask_decoder_int8.onnx_data": (
        HF + SAM_REV + "onnx/prompt_encoder_mask_decoder_int8.onnx_data",
        "fdb3179a32388267a944a416c0f992c77a138b69a4bfd94f0bbf7089430d7489", "Apache-2.0", 0),
}
APPROX_MB = 220

# Gesture control (vcstudio/ai/hands.py): MediaPipe palm detector + hand landmarks, ONNX from the OpenCV Zoo
# (Apache-2.0), about 8 MB, pinned commits. Downloaded with the same button rules (a click, SHA-256 checked).
PALM_REV = "opencv/palm_detection_mediapipe/resolve/233e619dcea1759bf6de707b9b904fe30881ea55/"
HANDPOSE_REV = "opencv/handpose_estimation_mediapipe/resolve/4b2a0b446e5cf2f11fb6b2c7251091c035d2c1f7/"
HANDS: dict[str, tuple[str, str, str, int]] = {
    "hands/palm_detection_mediapipe_2023feb.onnx": (
        HF + PALM_REV + "palm_detection_mediapipe_2023feb.onnx",
        "78ff51c38496b7fc8b8ebdb6cc8c1abb02fa6c38427c6848254cdaba57fcce7c", "Apache-2.0", 3905734),
    "hands/handpose_estimation_mediapipe_2023feb.onnx": (
        HF + HANDPOSE_REV + "handpose_estimation_mediapipe_2023feb.onnx",
        "db0898ae717b76b075d9bf563af315b29562e11f8df5027a1ef07b02bef6d81c", "Apache-2.0", 4099621),
}
HANDS_APPROX_MB = 8

# Body pose (vcstudio/ai/pose.py): MediaPipe person detector + 39-keypoint pose landmarker, ONNX from the OpenCV Zoo
# (Apache-2.0), about 17 MB, pinned commits.
PERSON_REV = "opencv/person_detection_mediapipe/resolve/531b6da87204aa40aa5cc680b4d032dd09b5055e/"
POSE_REV = "opencv/pose_estimation_mediapipe/resolve/8cd55743af63a8578effaf144a68cb58f6450fe9/"
POSE: dict[str, tuple[str, str, str, int]] = {
    "pose/person_detection_mediapipe_2023mar.onnx": (
        HF + PERSON_REV + "person_detection_mediapipe_2023mar.onnx",
        "47fd5599d6fa17608f03e0eb0ae230baa6e597d7e8a2c8199fe00abea55a701f", "Apache-2.0", 11990159),
    "pose/pose_estimation_mediapipe_2023mar.onnx": (
        HF + POSE_REV + "pose_estimation_mediapipe_2023mar.onnx",
        "9d89c599319a18fb7d2e28451a883476164543182bafca5f09eb2cf767ed2f3f", "Apache-2.0", 5557238),
}
POSE_APPROX_MB = 17

# Text regions for the privacy shield (vcstudio/ai/text_detect.py): PP-OCRv3 DB text detector (PaddlePaddle,
# Apache-2.0) from the OpenCV Zoo, about 2.4 MB, pinned commit. Finds where text is; recognition is separate.
TEXTDET_REV = "opencv/text_detection_ppocr/resolve/c299e81aedeaf5d4f741fccccea491fb32db005f/"
TEXT: dict[str, tuple[str, str, str, int]] = {
    "text/text_detection_en_ppocrv3_2023may.onnx": (
        HF + TEXTDET_REV + "text_detection_en_ppocrv3_2023may.onnx",
        "03f550c6b406fda8bf54bd8327815f6c7e2edd98cea02348c93d879254366587", "Apache-2.0", 2423490),
}
TEXT_APPROX_MB = 3

# Text recognition for "only sensitive text" (vcstudio/ai/text_detect.TextRecognizer): CRNN English, 8-bit, from the
# OpenCV Zoo (Apache-2.0), 16 MB, pinned commit. Reads 0-9 and a-z.
TEXTREC_REV = "opencv/text_recognition_crnn/resolve/6487ac545965a2c277aff293e2fdded8b0e6f9b9/"
TEXT_REC: dict[str, tuple[str, str, str, int]] = {
    "text/text_recognition_CRNN_EN_2022oct_int8.onnx": (
        HF + TEXTREC_REV + "text_recognition_CRNN_EN_2022oct_int8.onnx",
        "94117b4c2652337b3f1aef81b2ec15a74e97973b1c58f743e86380b95b95ffa2", "Apache-2.0", 16378030),
}
TEXT_REC_APPROX_MB = 16

# CLIP ViT-B/32 (OpenAI, MIT), 8-bit ONNX (Xenova, Transformers.js), pinned commit, about 147 MB: checks what OWLv2
# found against the query text, and finds objects like an example picture (vcstudio/ai/clip.py). The text model uses
# the CLIP tokenizer that comes with OWLv2 (same vocabulary), so it is only useful with the open-vocabulary models.
CLIP_REV = "Xenova/clip-vit-base-patch32/resolve/6b7c523703ae26e39be4b03774966f8571c7f523/"
CLIP: dict[str, tuple[str, str, str, int]] = {
    "clip/vision_model_int8.onnx": (HF + CLIP_REV + "onnx/vision_model_quantized.onnx",
                                    "583fd1110a514667812fee7d684952aaf82a99b959760c8d7dca7e0ab9839299", "MIT", 89117001),
    "clip/text_model_int8.onnx": (HF + CLIP_REV + "onnx/text_model_quantized.onnx",
                                  "73baab855d406190da9faa498cfedf65f15cf309f4cc7385b7b032e6d08e5c3a", "MIT", 64504507),
}
CLIP_APPROX_MB = 147


class DownloadError(RuntimeError):
    pass


class Cancelled(DownloadError):
    pass


def network_allowed(settings=None) -> bool:
    if os.environ.get("VCS_NO_NETWORK", "").strip() not in ("", "0"):
        return False
    if settings is not None:
        try:
            return bool(settings.get("privacy.allow_downloads"))
        except KeyError:
            return True
    return True


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def missing(dest: Path, table: dict[str, tuple[str, str, str, int]] | None = None) -> list[str]:
    """Files of `table` not present in `dest` (presence only: the hash was checked when they were written)."""
    return [name for name in (OPEN_VOCAB if table is None else table) if not (dest / name).exists()]


Progress = Callable[[str, int, int], None]          # (file, bytes done in this file, total bytes or 0)


def download(dest: Path, progress: Progress | None = None, cancel: threading.Event | None = None,
             table: dict[str, tuple[str, str, str, int]] | None = None,
             opener: Callable[[str], object] | None = None) -> list[str]:
    """Download the missing files of `table` (default OPEN_VOCAB) into `dest`; returns the names downloaded. Raises
    Cancelled or DownloadError (network, SHA-256 mismatch); a failed file never stays under its final name."""
    table = OPEN_VOCAB if table is None else table
    opener = opener or (lambda url: urllib.request.urlopen(url, timeout=60))      # noqa: S310 (fixed https URLs)
    done: list[str] = []
    for name in missing(dest, table):
        url, digest, _lic, size = table[name]
        if not url.startswith("https://huggingface.co/"):
            raise DownloadError(f"{name}: unexpected source {url}")
        dst = dest / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".part")
        log.info("model download: %s <- %s", name, url)
        try:
            with opener(url) as r, tmp.open("wb") as f:          # type: ignore[attr-defined]
                total = int(getattr(r, "headers", {}).get("Content-Length") or size or 0)
                got = 0
                while True:
                    if cancel is not None and cancel.is_set():
                        raise Cancelled("cancelled")
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    if progress:
                        progress(name, got, total)
            if sha256(tmp) != digest:
                raise DownloadError(f"{name}: checksum mismatch (the file was removed)")
            tmp.replace(dst)
            done.append(name)
        except DownloadError:                                        # Cancelled included
            raise
        except Exception as e:                                       # noqa: BLE001 (network / disk errors)
            raise DownloadError(f"{name}: {e}") from e
        finally:
            if tmp.exists():
                tmp.unlink()
    return done
