"""Image decoding on Windows (replaces Apple ImageIO): premultiplied BGRA uint8, like the macOS path.

OpenCV decodes PNG, JPEG, WEBP, BMP, TIFF; Pillow covers GIF (first frame) and anything else it can
open (HEIC/AVIF when the pillow-heif / pillow-avif plugins are installed). Files are read as bytes and
decoded from memory, because cv2.imread cannot open paths with non-ASCII characters on Windows.
16-bit images are reduced to 8 bit; grey and RGB images get an opaque alpha channel. As with ImageIO,
the EXIF orientation flag is not applied (identical results on both platforms).
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


class ImageDecodeError(ValueError):
    pass


def premultiply(a: np.ndarray) -> np.ndarray:
    alpha = a[..., 3:4].astype(np.uint16)
    out = a.copy()
    out[..., :3] = ((a[..., :3].astype(np.uint16) * alpha + 127) // 255).astype(np.uint8)
    return out


def _to_bgra8(img: np.ndarray) -> np.ndarray:
    if img.dtype == np.uint16:
        img = (img >> 8).astype(np.uint8)
    elif img.dtype != np.uint8:
        img = cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)  # type: ignore[call-overload]
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
    if img.shape[2] == 3:
        return cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
    if img.shape[2] == 4:
        return img
    raise ImageDecodeError(f"unsupported channel count {img.shape[2]}")


def _pillow(path: Path) -> np.ndarray | None:
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(path) as im:
            im.seek(0)
            rgba = np.asarray(im.convert("RGBA"))
    except Exception:
        return None
    return cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA)


def decode_bgra(path: str | Path, max_side: int | None = None, premultiplied: bool = True) -> np.ndarray:
    """Decode an image file to (H, W, 4) uint8 BGRA (premultiplied by default), downscaled to max_side."""
    p = Path(path)
    try:
        data = np.fromfile(str(p), dtype=np.uint8)
    except OSError as e:
        raise ImageDecodeError(f"Cannot read image: {p.name} ({e})") from e
    img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED) if data.size else None
    bgra = _to_bgra8(img) if img is not None else _pillow(p)
    if bgra is None:
        raise ImageDecodeError(f"Cannot decode image: {p.name}")
    h, w = bgra.shape[:2]
    if max_side and max(w, h) > max_side:
        s = max_side / max(w, h)
        bgra = cv2.resize(bgra, (max(1, int(round(w * s))), max(1, int(round(h * s)))), interpolation=cv2.INTER_AREA)
    bgra = np.ascontiguousarray(bgra)
    return premultiply(bgra) if premultiplied else bgra
