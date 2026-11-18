"""CPU compositor (the fallback when no GPU backend works): the 8-bit OpenCV implementation matches the float
formula from the compositor's header to within a level or two, keeps the frame's alpha and leaves the input alone."""
from __future__ import annotations

import math

import cv2
import numpy as np
import pytest

from vcstudio.replacement.compositor import CpuBackend, DrawOp

RNG = np.random.default_rng(7)
H, W = 360, 640


def scene():
    f = np.zeros((H, W, 4), np.uint8)
    yy, xx = np.mgrid[0:H, 0:W]
    f[..., 0] = (xx * 255 // W).astype(np.uint8)
    f[..., 1] = (yy * 255 // H).astype(np.uint8)
    f[..., 2] = ((xx + yy) % 256).astype(np.uint8)
    f[..., 3] = 200                                   # not 255: the backend must keep whatever alpha the frame has
    f[::7, ::5, :3] = 255                             # some detail for blur/pixelate
    return f


def premul_logo(h=90, w=160):
    rgb = RNG.integers(0, 256, (h, w, 3), dtype=np.uint8)
    a = np.zeros((h, w), np.uint8)
    cv2.circle(a, (w // 2, h // 2), min(h, w) // 2 - 4, 255, -1)
    a = cv2.GaussianBlur(a, (0, 0), 3)                # soft edge: partial alpha
    prem = (rgb.astype(np.float32) * (a[..., None] / 255.0) + 0.5).astype(np.uint8)
    return np.dstack([prem, a])


def reference(frame, ops):
    """The float formula (previous implementation): composite inner = src + bg (1 - src.a);
    out = k inner + dst (1 - k inner.a); effect out = k eff + dst (1 - k)."""
    out = frame.copy()
    for op in ops:
        x0, y0, x1, y1 = op.bbox
        w, h = x1 - x0, y1 - y0
        dst = out[y0:y1, x0:x1, :3].astype(np.float32) / 255
        k = np.full((h, w, 1), op.opacity, np.float32)
        if op.mask is not None:
            m = op.mask if op.mask_rect is None else CpuBackend._place(op.mask, op.mask_rect, op.bbox)
            k *= m[..., None].astype(np.float32) / 255
        if op.kind == "composite":
            M = op.inv.copy(); M[:, 2] += M[:, :2] @ np.array([x0, y0], dtype=np.float64)
            patch = cv2.warpAffine(op.src, M, (w, h), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                                   borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0)).astype(np.float32) / 255
            inner = patch + np.array(op.bg, np.float32) * (1 - patch[..., 3:4])
            res = inner[..., :3] * k + dst * (1 - k * inner[..., 3:4])
        else:
            res = CpuBackend._effect(frame, op, W, H) * k + dst * (1 - k)
        out[y0:y1, x0:x1, :3] = np.clip(res * 255 + 0.5, 0, 255).astype(np.uint8)
    return out


def composite(bbox, *, rot=0.0, opacity=1.0, bg=(0, 0, 0, 0), mask=None, mask_rect=None):
    src = premul_logo()
    x0, y0, x1, y1 = bbox
    sh, sw = src.shape[:2]
    c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    sx, sy = (x1 - x0) / sw, (y1 - y0) / sh
    M = np.array([[c * sx, -s * sy, 0], [s * sx, c * sy, 0]], np.float64)
    M[:, 2] = np.array([(x0 + x1) / 2, (y0 + y1) / 2]) - M[:, :2] @ np.array([sw / 2, sh / 2])
    return DrawOp("r", "composite", bbox, opacity, mask=mask, mask_rect=mask_rect, src=src,
                  inv=cv2.invertAffineTransform(M), bg=bg)


def soft_mask(h, w):
    m = np.zeros((h, w), np.uint8)
    cv2.ellipse(m, (w // 2, h // 2), (w // 2 - 3, h // 2 - 3), 0, 0, 360, 255, -1)
    return cv2.GaussianBlur(m, (0, 0), 4)


CASES = {
    "overlay": [composite((40, 30, 300, 200))],
    "rotated-half-opacity": [composite((100, 60, 420, 300), rot=23, opacity=0.6)],
    "opaque-fill-and-mask": [composite((200, 100, 500, 340), bg=(0.2, 0.0, 0.8, 1.0), mask=soft_mask(240, 300))],
    "half-transparent-fill": [composite((10, 10, 250, 170), bg=(0.25, 0.25, 0.0, 0.5))],
    "mask-on-other-grid": [composite((50, 50, 330, 290), mask=soft_mask(120, 140), mask_rect=(48.5, 47.2, 331.0, 292.4))],
    "pixelate": [DrawOp("e", "effect", (37, 41, 299, 251), 1.0, mask=soft_mask(210, 262), effect="pixelate",
                        params=(16, 0, 0, 0))],
    "color": [DrawOp("e", "effect", (300, 20, 620, 300), 0.8, effect="color", params=(0.1, 1.4, 0.2, 0))],
    "blur-narrow": [DrawOp("e", "effect", (0, 0, 320, 200), 1.0, effect="blur", params=(3.0, 0, 0, 0))],
    "layers": [composite((40, 30, 300, 200)), DrawOp("e", "effect", (150, 80, 450, 330), 1.0, effect="pixelate",
                                                     params=(12, 0, 0, 0)),
               composite((260, 120, 600, 350), rot=-12, bg=(0, 0.5, 0, 1.0))],
}


@pytest.mark.parametrize("name", list(CASES))
def test_matches_the_float_formula(name):
    frame = scene(); before = frame.copy()
    ops = CASES[name]
    got, ref = CpuBackend().execute(frame, ops), reference(frame, ops)
    d = np.abs(got.astype(int) - ref.astype(int))[..., :3]
    assert d.max() <= 2 and d.mean() < 0.15, (name, d.max(), d.mean())    # 8-bit steps round once more
    assert np.array_equal(got[..., 3], frame[..., 3])            # frame alpha kept
    assert np.array_equal(frame, before)                         # input untouched
    assert not np.array_equal(got, frame)                        # something was drawn


def test_empty_and_offscreen_boxes_are_skipped():
    frame = scene()
    op = composite((40, 30, 300, 200)); op.bbox = (100, 100, 100, 180)
    assert np.array_equal(CpuBackend().execute(frame, [op]), frame)


def test_three_channel_frames_work():
    frame = np.ascontiguousarray(scene()[..., :3])
    ops = [DrawOp("e", "effect", (10, 10, 200, 150), 1.0, effect="pixelate", params=(10, 0, 0, 0)),
           DrawOp("e", "effect", (100, 50, 300, 250), 0.5, effect="color", params=(0.0, 1.2, 0.5, 0))]
    out = CpuBackend().execute(frame, ops)
    assert out.shape == frame.shape and not np.array_equal(out, frame)


@pytest.mark.parametrize("sigma", [2, 7, 12, 30])
def test_wide_blur_on_a_smaller_copy_matches_the_full_blur(sigma):
    frame = scene()
    rng = np.random.default_rng(1)
    frame[..., :3] = cv2.GaussianBlur(rng.integers(0, 256, (H, W, 3), dtype=np.uint8), (0, 0), 2)  # photo-like
    box = (120, 60, 520, 300)
    got = CpuBackend.blur8(frame, box, sigma, W, H)
    m = int(math.ceil(3 * sigma))
    full = cv2.GaussianBlur(frame[max(0, 60 - m):min(H, 300 + m), max(0, 120 - m):min(W, 520 + m)], (0, 0),
                            sigmaX=sigma, borderType=cv2.BORDER_REPLICATE)
    oy, ox = 60 - max(0, 60 - m), 120 - max(0, 120 - m)
    ref = full[oy:oy + 240, ox:ox + 400]
    d = np.abs(got.astype(int) - ref.astype(int))
    psnr = 10 * np.log10(255 ** 2 / max(1e-9, (d.astype(float) ** 2).mean()))
    assert got.shape == (240, 400, 4) and got.flags.c_contiguous
    assert psnr > 45 and d.mean() < 1.0, (sigma, d.max(), psnr)
    if sigma < 6:
        assert d.max() == 0                                       # narrow blur: exact
