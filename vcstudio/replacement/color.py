"""Colour Studio (0.6.0 Phase 6): grading controls and .cube LUTs, per scene (profile section "color").

Processing order (documented; applied ONCE per output frame, to the camera picture, after automatic lighting and
before backgrounds, regions and overlays — so replacement images keep their own colours and a scene switch never
grades twice: the stage is stateless apart from caches keyed by the settings):
  1. tone        exposure (EV), brightness, contrast, gamma, shadows, highlights, temperature, tint
                 -> folded into three 256-entry tables, one cv2.LUT pass (exact, ~2 ms per 1080p frame here)
  2. saturation  mixing with the grey picture; with skin protection, skin-coloured pixels (YCrCb range, at quarter
                 size) keep most of their original saturation
  3. 3D LUT      a .cube file (LUT_3D_SIZE 2..129, or LUT_1D_SIZE): resampled once to 64³; applied at half size
                 (a third above 720p) and added as a colour difference to the full picture (like 4:2:0 chroma: sharp
                 detail stays from the full-size frame). `lut_intensity` blends it. The direct per-pixel lookup cost
                 40 ms per 1080p frame on the 2-core server.
                 GPU path: with a hardware Direct3D 11 device the LUT runs in vcs_d3d11.dll instead (Texture3D,
                 hardware trilinear filtering, full resolution: mean error 0.19 levels against an exact trilinear
                 lookup, the CPU path 3.3). On WARP (no GPU) it is not faster than the CPU path (20.8 vs 18.4 ms at
                 1080p here), so "auto" uses it only on hardware; any GPU error falls back to the CPU path for good
  4. sharpness   unsharp mask (Gaussian at quarter size, upscaled)
  5. effects     vignette (cached radial gain), soft glow (blurred highlights added), film grain (cached noise
                 tiles, moved every frame), tone mapping is the highlights control (soft shoulder)
Every step is skipped when it is neutral, so a neutral profile costs nothing.

Frame-rate guard: the stage runs on the output thread, so it must not cost the camera its frame rate. When it takes
more than `budget_ms` (half a frame interval, given by the pipeline) for OVER_FRAMES frames in a row, the grader goes
"reduced": sharpness, glow, film grain and skin protection are skipped (tone, saturation, LUT and vignette stay).
It tries the full set again after RETRY_FRAMES frames; the UI shows when it is reduced. Measured on the 2-core
server: everything on at 1080p cost ~38 ms per frame and held the output at 26 fps; reduced, the output stays at the
input rate.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, fields
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)

LUT_GRID = 64
MAX_LUT_BYTES = 30 * 2**20

RANGES = {                      # name: (lo, hi, neutral)
    "exposure": (-2.0, 2.0, 0.0), "brightness": (-1.0, 1.0, 0.0), "contrast": (0.0, 2.0, 1.0),
    "saturation": (0.0, 2.0, 1.0), "temperature": (-1.0, 1.0, 0.0), "tint": (-1.0, 1.0, 0.0),
    "gamma": (0.4, 2.5, 1.0), "highlights": (-1.0, 1.0, 0.0), "shadows": (-1.0, 1.0, 0.0),
    "sharpness": (0.0, 2.0, 0.0), "vignette": (0.0, 1.0, 0.0), "grain": (0.0, 1.0, 0.0), "glow": (0.0, 1.0, 0.0),
    "lut_intensity": (0.0, 1.0, 1.0),
}


@dataclass(frozen=True)
class ColorConfig:
    enabled: bool = False
    exposure: float = 0.0
    brightness: float = 0.0
    contrast: float = 1.0
    saturation: float = 1.0
    temperature: float = 0.0
    tint: float = 0.0
    gamma: float = 1.0
    highlights: float = 0.0
    shadows: float = 0.0
    sharpness: float = 0.0
    vignette: float = 0.0
    grain: float = 0.0
    glow: float = 0.0
    skin_protect: bool = False
    lut: str = ""                       # path of a .cube file ("" = none)
    lut_intensity: float = 1.0

    @staticmethod
    def from_profile(body: dict) -> ColorConfig:
        d = body.get("color") or {}
        if not isinstance(d, dict):
            return ColorConfig()
        kw: dict = {"enabled": bool(d.get("enabled", False)), "skin_protect": bool(d.get("skin_protect", False)),
                    "lut": str(d.get("lut") or "")}
        for name, (lo, hi, neutral) in RANGES.items():
            try:
                kw[name] = round(min(hi, max(lo, float(d.get(name, neutral)))), 4)
            except (TypeError, ValueError):
                kw[name] = neutral
        return ColorConfig(**kw)

    def as_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @property
    def tone_neutral(self) -> bool:
        return all(getattr(self, k) == RANGES[k][2] for k in ("exposure", "brightness", "contrast", "temperature",
                                                               "tint", "gamma", "highlights", "shadows"))

    @property
    def active(self) -> bool:
        if not self.enabled:
            return False
        return not (self.tone_neutral and self.saturation == 1.0 and not self.lut and self.sharpness == 0 and
                    self.vignette == 0 and self.grain == 0 and self.glow == 0)


# ----------------------------------------------------------------------------------------------- .cube
class LutError(ValueError):
    pass


def parse_cube(text: str) -> tuple[str, np.ndarray, tuple[float, float]]:
    """('3d', (N, N, N, 3) float32 indexed [b][g][r] or '1d', (N, 3)) and the input domain. Raises LutError."""
    size3 = size1 = 0
    lo, hi = 0.0, 1.0
    vals: list[list[float]] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        head = line.split()[0].upper()
        if head == "TITLE":
            continue
        if head == "LUT_3D_SIZE":
            size3 = int(line.split()[1])
        elif head == "LUT_1D_SIZE":
            size1 = int(line.split()[1])
        elif head == "DOMAIN_MIN":
            lo = float(line.split()[1])
        elif head == "DOMAIN_MAX":
            hi = float(line.split()[1])
        elif head[0].isdigit() or head[0] in "-.":
            parts = line.split()
            if len(parts) != 3:
                raise LutError(f"bad data line: {raw!r}")
            vals.append([float(x) for x in parts])
        elif head.startswith("LUT_") or head in ("DOMAIN_MIN", "DOMAIN_MAX"):
            continue
        else:
            raise LutError(f"unknown keyword {head}")
    if hi <= lo:
        raise LutError("DOMAIN_MAX must be above DOMAIN_MIN")
    arr = np.asarray(vals, np.float32)
    if size3:
        if not 2 <= size3 <= 129 or len(arr) != size3 ** 3:
            raise LutError(f"LUT_3D_SIZE {size3} needs {size3 ** 3} entries, found {len(arr)}")
        return "3d", arr.reshape(size3, size3, size3, 3), (lo, hi)        # .cube: red fastest -> [b][g][r]
    if size1:
        if not 2 <= size1 <= 65536 or len(arr) != size1:
            raise LutError(f"LUT_1D_SIZE {size1} needs {size1} entries, found {len(arr)}")
        return "1d", arr, (lo, hi)
    raise LutError("no LUT_3D_SIZE or LUT_1D_SIZE")


def _trilinear(lut: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Sample an (N, N, N, 3) [b][g][r] LUT at pts (M, 3) rgb in 0..1."""
    n = lut.shape[0]
    p = np.clip(pts, 0, 1) * (n - 1)
    i0 = np.floor(p).astype(np.int32)
    i1 = np.minimum(i0 + 1, n - 1)
    f = p - i0
    r0, g0, b0 = i0[:, 0], i0[:, 1], i0[:, 2]
    r1, g1, b1 = i1[:, 0], i1[:, 1], i1[:, 2]
    fr, fg, fb = f[:, 0:1], f[:, 1:2], f[:, 2:3]
    c00 = lut[b0, g0, r0] * (1 - fr) + lut[b0, g0, r1] * fr
    c01 = lut[b0, g1, r0] * (1 - fr) + lut[b0, g1, r1] * fr
    c10 = lut[b1, g0, r0] * (1 - fr) + lut[b1, g0, r1] * fr
    c11 = lut[b1, g1, r0] * (1 - fr) + lut[b1, g1, r1] * fr
    return (c00 * (1 - fg) + c01 * fg) * (1 - fb) + (c10 * (1 - fg) + c11 * fg) * fb


@lru_cache(maxsize=4)
def load_lut(path: str, mtime: float) -> tuple[str, np.ndarray]:
    """('3d', (64³, 4) uint8 BGRA table indexed b<<12|g<<6|r) or ('1d', (256, 1, 3) uint8 per-channel tables)."""
    p = Path(path)
    if p.stat().st_size > MAX_LUT_BYTES:
        raise LutError("LUT file too large")
    kind, data, (lo, hi) = parse_cube(p.read_text(encoding="utf-8", errors="replace"))
    if kind == "1d":
        x = np.linspace(lo, hi, len(data))
        grid = np.arange(256) / 255.0
        tab = np.stack([np.interp(grid, x, data[:, c]) for c in range(3)], 1)     # r, g, b columns
        return "1d", (np.clip(tab, 0, 1) * 255 + 0.5).astype(np.uint8)[:, ::-1].reshape(256, 1, 3)
    g = np.linspace(0, 1, LUT_GRID, dtype=np.float32)
    bb, gg, rr = np.meshgrid(g, g, g, indexing="ij")
    pts = np.stack([rr.ravel(), gg.ravel(), bb.ravel()], 1) * (hi - lo) + lo
    rgb = _trilinear(data, (pts - lo) / (hi - lo))
    out = np.empty((LUT_GRID ** 3, 4), np.uint8)
    out[:, :3] = (np.clip(rgb[:, ::-1], 0, 1) * 255 + 0.5).astype(np.uint8)       # store BGR
    out[:, 3] = 255
    return "3d", out


# ----------------------------------------------------------------------------------------------- tone tables
@lru_cache(maxsize=16)
def tone_tables(exposure, brightness, contrast, gamma, shadows, highlights, temperature, tint) -> np.ndarray:
    """(256, 1, 3) uint8 B, G, R tables for cv2.LUT (pure, cached by the settings)."""
    x = np.arange(256, dtype=np.float64) / 255.0
    y = x * (2.0 ** exposure)
    y = y + brightness * 0.5
    y = (y - 0.5) * contrast + 0.5
    y = np.clip(y, 0, None)
    if shadows:
        y = y + shadows * 0.25 * np.exp(-((y / 0.25) ** 2)) * (y < 0.6)
    if highlights < 0:                                 # tone mapping: a soft shoulder instead of clipping at white
        knee = 0.75
        over = y > knee
        y = np.where(over, knee + (1 - knee) * (1 - np.exp(-(y - knee) / (1 - knee))), y)
    if highlights:                                     # then the upper half bends down (< 0) or up (> 0)
        u = np.clip((y - 0.5) * 2, 0, 1)
        y = np.where(y > 0.5, y + highlights * 0.2 * u * (1 - u) * 2, y)
    y = np.clip(y, 0, 1) ** (1.0 / gamma)
    gains = (1 - 0.15 * temperature + 0.05 * tint, 1 - 0.1 * tint, 1 + 0.15 * temperature + 0.05 * tint)  # B, G, R
    tabs = [np.clip(y * g, 0, 1) for g in gains]
    return (np.stack(tabs, 1) * 255 + 0.5).astype(np.uint8).reshape(256, 1, 3)


def _skin_mask_small(bgr_small: np.ndarray) -> np.ndarray:
    ycc = cv2.cvtColor(bgr_small, cv2.COLOR_BGR2YCrCb)
    m = cv2.inRange(ycc, (0, 135, 85), (255, 180, 135))
    return cv2.GaussianBlur(m, (0, 0), 2)


@lru_cache(maxsize=4)
def _vignette_gain(w: int, h: int, amount: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(gain, 1 - gain, black frame) for cv2.blendLinear: the SIMD float blend is ~4x faster here than
    cv2.multiply with a scale."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    r = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2) / math.sqrt(2)
    g = (1.0 - amount * np.clip((r - 0.35) / 0.65, 0, 1) ** 2).astype(np.float32)
    black = np.zeros((h, w, 4), np.uint8)
    return g, np.ascontiguousarray(1.0 - g), black


@lru_cache(maxsize=2)
def _grain_tiles(n: int = 256) -> tuple[np.ndarray, np.ndarray]:
    """(positive, negative) uint8 BGRA noise tiles of unit amplitude ×16 (alpha 0): added / subtracted with
    saturation, so grain never needs a wider integer type."""
    rng = np.random.default_rng(1234)
    t = cv2.GaussianBlur(rng.normal(0, 1, (n, n)).astype(np.float32), (0, 0), 0.7)
    t = t / max(1e-6, float(t.std())) * 16
    pos = np.clip(t, 0, 255).astype(np.uint8)
    neg = np.clip(-t, 0, 255).astype(np.uint8)
    z = np.zeros_like(pos)
    return cv2.merge([pos, pos, pos, z]), cv2.merge([neg, neg, neg, z])


@lru_cache(maxsize=2)
def _grain_field(w: int, h: int, k: float) -> tuple[np.ndarray, np.ndarray]:
    """Noise tiles scaled by k and repeated to cover the frame plus one tile: each frame only takes a shifted slice."""
    pos, neg = _grain_tiles()
    n = pos.shape[0]
    reps = (h // n + 2, w // n + 2, 1)
    sp = (pos.astype(np.float32) * k + 0.5).astype(np.uint8)
    sn = (neg.astype(np.float32) * k + 0.5).astype(np.uint8)
    return np.tile(sp, reps), np.tile(sn, reps)


def _alpha_table(tab3: np.ndarray) -> np.ndarray:
    """(256, 1, 3) B, G, R tables -> (256, 1, 4) with an identity alpha table (cv2.LUT on BGRA directly)."""
    ident = np.arange(256, dtype=np.uint8).reshape(256, 1, 1)
    return np.ascontiguousarray(np.concatenate([tab3, ident], 2))


OVER_FRAMES = 8
RETRY_FRAMES = 600


class ColorGrader:
    """Applies a ColorConfig to BGRA frames; stateless apart from caches keyed by the settings.

    Everything stays in 4-channel uint8 (cv2.LUT, addWeighted, add / subtract with saturation): no BGR copies and no
    wider integer types, which halved the cost on the 2-core server."""

    def __init__(self):
        self.frames = 0
        self.ms = 0.0
        self.error = ""
        self.reduced = False                  # frame-rate guard: expensive effects skipped
        self.gpu_mode = "auto"                # 3-D LUT on Direct3D 11: auto (hardware GPU only) | on | off
        self.gpu_used = False                 # the last LUT frame ran on the GPU
        self._gpu = None                      # D3D11Context, created on the output thread on first use
        self._gpu_failed = ""
        self._gpu_lut_key = None
        self._over = 0
        self._since_reduced = 0

    def _guard(self, ms: float, budget_ms: float | None) -> None:
        if budget_ms is None:
            return
        if self.reduced:
            self._since_reduced += 1
            if self._since_reduced >= RETRY_FRAMES:          # try the full set again now and then
                self.reduced, self._over, self._since_reduced = False, 0, 0
            return
        self._over = self._over + 1 if ms > budget_ms else 0
        if self._over >= OVER_FRAMES:
            self.reduced, self._since_reduced = True, 0
            log.info("colour grading took %.1f ms (budget %.1f): effects reduced to keep the frame rate", ms, budget_ms)

    def apply(self, bgra: np.ndarray, cfg: ColorConfig, t: float = 0.0, budget_ms: float | None = None) -> np.ndarray:
        import time
        if not cfg.active:
            return bgra
        if self.reduced:
            from dataclasses import replace
            cfg = replace(cfg, sharpness=0.0, glow=0.0, grain=0.0, skin_protect=False)
            if not cfg.active:
                return bgra
        t0 = time.perf_counter()
        h, w = bgra.shape[:2]
        out = np.ascontiguousarray(bgra)
        src_is_input = True
        if not cfg.tone_neutral:
            tab = _alpha_table(tone_tables(cfg.exposure, cfg.brightness, cfg.contrast, cfg.gamma, cfg.shadows,
                                           cfg.highlights, cfg.temperature, cfg.tint))
            out = cv2.LUT(out, tab)
            src_is_input = False
        if cfg.saturation != 1.0:
            gray = cv2.cvtColor(cv2.cvtColor(out, cv2.COLOR_BGRA2GRAY), cv2.COLOR_GRAY2BGRA)
            sat = cv2.addWeighted(out, cfg.saturation, gray, 1.0 - cfg.saturation, 0)
            if cfg.skin_protect:
                small = cv2.resize(out, (max(1, w // 4), max(1, h // 4)), interpolation=cv2.INTER_AREA)
                m = _skin_mask_small(cv2.cvtColor(small, cv2.COLOR_BGRA2BGR)).astype(np.float32) * (0.75 / 255)
                m = cv2.resize(m, (w, h), interpolation=cv2.INTER_LINEAR)
                sat = cv2.blendLinear(out, sat, m, 1.0 - m)            # skin keeps 75 % of its own colour
            out = sat
            src_is_input = False
        if cfg.lut:
            out = self._lut(out, cfg)
            src_is_input = False
        if cfg.sharpness > 0:
            small = cv2.resize(out, (max(1, w // 2), max(1, h // 2)), interpolation=cv2.INTER_AREA)
            blur = cv2.resize(cv2.GaussianBlur(small, (0, 0), 1.2), (w, h), interpolation=cv2.INTER_LINEAR)
            out = cv2.addWeighted(out, 1.0 + cfg.sharpness, blur, -cfg.sharpness, 0)
            src_is_input = False
        if cfg.glow > 0:
            small = cv2.resize(out, (max(1, w // 8), max(1, h // 8)), interpolation=cv2.INTER_AREA)
            hi = cv2.threshold(small, 170, 255, cv2.THRESH_TOZERO)[1]
            hi[..., 3] = 0
            glow = cv2.resize(cv2.GaussianBlur(hi, (0, 0), 3), (w, h), interpolation=cv2.INTER_LINEAR)
            out = cv2.addWeighted(out, 1.0, glow, 0.6 * cfg.glow, 0)
            src_is_input = False
        if cfg.vignette > 0:
            g, ig, black = _vignette_gain(w, h, round(cfg.vignette, 2))
            out = cv2.blendLinear(out, black, g, ig)
            src_is_input = False
        if cfg.grain > 0:
            pos, neg = _grain_field(w, h, round(cfg.grain * 0.75, 2))   # 16 × 0.75 = 12 levels at full strength
            n = _grain_tiles()[0].shape[0]
            ox, oy = int(t * 997) % n, int(t * 613) % n
            out = cv2.subtract(cv2.add(out, pos[oy:oy + h, ox:ox + w]), neg[oy:oy + h, ox:ox + w])
            src_is_input = False
        if src_is_input:
            return bgra
        if out is not bgra:
            out[..., 3] = bgra[..., 3]                     # alpha is the input's, whatever a step did
        self.frames += 1
        ms = (time.perf_counter() - t0) * 1000
        self.ms = ms if self.frames == 1 else 0.9 * self.ms + 0.1 * ms
        self._guard(ms, budget_ms)
        return out

    def _gpu_ctx(self):
        if self.gpu_mode == "off" or self._gpu_failed:
            return None
        if self._gpu is None:
            try:
                from vcstudio.platform import IS_WINDOWS
                if not IS_WINDOWS:
                    self._gpu_failed = "not Windows"
                    return None
                from vcstudio.platform.win.d3d11 import D3D11Context, available
                if not available():
                    self._gpu_failed = "vcs_d3d11.dll not available"
                    return None
                ctx = D3D11Context()
                if not ctx.has_lut or (ctx.software and self.gpu_mode != "on"):
                    ctx.close()
                    self._gpu_failed = "software device (WARP): the CPU path is as fast" if ctx.has_lut else \
                        "this vcs_d3d11.dll has no LUT pass"
                    return None
                self._gpu = ctx
                log.info("colour LUT on the GPU: %s", ctx.device_name)
            except Exception as e:                           # noqa: BLE001
                self._gpu_failed = f"{type(e).__name__}: {e}"
                return None
        return self._gpu

    def _close_gpu(self) -> None:
        if self._gpu is not None:
            try:
                self._gpu.close()
            except Exception:
                pass
        self._gpu, self._gpu_lut_key = None, None

    def _lut(self, bgra: np.ndarray, cfg: ColorConfig) -> np.ndarray:
        try:
            kind, tab = load_lut(cfg.lut, Path(cfg.lut).stat().st_mtime)
        except (OSError, LutError) as e:
            if self.error != str(e):
                log.warning("LUT %s not applied: %s", cfg.lut, e)
            self.error = str(e)
            return bgra
        k = cfg.lut_intensity
        if kind == "1d":
            graded = cv2.LUT(bgra, _alpha_table(tab))
        else:
            gpu = self._gpu_ctx()
            if gpu is not None:
                try:
                    key = (cfg.lut, Path(cfg.lut).stat().st_mtime)
                    if self._gpu_lut_key != key:
                        gpu.upload_lut(tab, LUT_GRID)
                        self._gpu_lut_key = key
                    self.gpu_used = True
                    return gpu.apply_lut(bgra, k)
                except Exception as e:                       # noqa: BLE001 - never lose the frame over the GPU
                    self._gpu_failed = f"{type(e).__name__}: {e}"
                    log.warning("GPU LUT failed, using the CPU path from now on: %s", self._gpu_failed)
                    self._close_gpu()
            self.gpu_used = False
            h, w = bgra.shape[:2]
            f = 2 if h <= 720 else 3                        # 1080p: a third of the size (~11 ms instead of ~24)
            small = cv2.resize(bgra, (max(1, w // f), max(1, h // f)), interpolation=cv2.INTER_AREA)
            q = small >> 2
            idx = (q[..., 0].astype(np.int32) << 12) | (q[..., 1].astype(np.int32) << 6) | q[..., 2]
            mapped = tab[idx]
            up = cv2.subtract(mapped, small)                # colour difference as two saturating uint8 images
            down = cv2.subtract(small, mapped)
            up[..., 3] = 0
            down[..., 3] = 0
            graded = cv2.subtract(cv2.add(bgra, cv2.resize(up, (w, h), interpolation=cv2.INTER_LINEAR)),
                                  cv2.resize(down, (w, h), interpolation=cv2.INTER_LINEAR))
        return graded if k >= 0.999 else cv2.addWeighted(graded, k, bgra, 1.0 - k, 0)


def import_lut(src: Path, luts_dir: Path) -> Path:
    """Validate a .cube file (parsed in full, nothing executed) and copy it into the app's LUT folder; profiles then
    refer to the copy, so moving or deleting the original does not break a scene."""
    import shutil
    src = Path(src)
    if src.suffix.lower() != ".cube":
        raise LutError("choose a .cube file")
    if src.stat().st_size > MAX_LUT_BYTES:
        raise LutError("LUT file too large")
    parse_cube(src.read_text(encoding="utf-8", errors="replace"))
    luts_dir.mkdir(parents=True, exist_ok=True)
    stem = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in src.stem)[:60] or "lut"
    dest = luts_dir / f"{stem}.cube"
    n = 2
    while dest.exists():
        dest = luts_dir / f"{stem}-{n}.cube"
        n += 1
    shutil.copy2(src, dest)
    return dest
