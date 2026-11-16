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
