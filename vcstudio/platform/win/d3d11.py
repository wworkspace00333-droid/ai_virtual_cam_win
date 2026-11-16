"""Direct3D 11 compositor backend (Windows): ctypes binding of native/win/d3d11/vcs_d3d11.dll.

The DLL runs the same per-layer maths as CpuBackend in compute shaders (see the .cpp header). This
module reuses MetalBackend's planning-to-layers code (texture slots, persistent image uploads,
per-frame video and mask uploads), so both GPU backends receive identical layer records.

Frames are BGRA NumPy arrays on Windows: the input is uploaded, layers run on the GPU, the result is
read back into a pooled output array (one upload + one read-back per frame; the virtual camera and
the previews take arrays). Without a hardware GPU the DLL uses WARP (Direct3D's software rasteriser,
multi-threaded SIMD); the backend name then says so and Settings can still force the CPU path.
"""
from __future__ import annotations

import ctypes
import logging
import threading

import numpy as np

from vcstudio.replacement.compositor import DrawOp, MetalBackend

log = logging.getLogger(__name__)

_lib = None
_lib_error = ""
_lock = threading.Lock()


def _load():
    global _lib, _lib_error
    with _lock:
        if _lib is not None or _lib_error:
            return _lib
        from vcstudio.core.resources import native_lib
        p = native_lib("vcs_d3d11.dll")
        if not p.exists():
            _lib_error = f"{p.name} not found (run scripts/build_native_win.ps1)"
            return None
        try:
            lib = ctypes.CDLL(str(p))
        except OSError as e:
            _lib_error = f"{p}: {e}"
            return None
        lib.vcs_create.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        lib.vcs_create.restype = ctypes.c_void_p
        lib.vcs_destroy.argtypes = [ctypes.c_void_p]
        lib.vcs_device_name.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        lib.vcs_last_error.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        lib.vcs_upload.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int]
        lib.vcs_upload.restype = ctypes.c_int
        lib.vcs_release_slot.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.vcs_render.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                   ctypes.c_void_p]
        lib.vcs_render.restype = ctypes.c_int
        lib.vcs_flush.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.vcs_is_warp.argtypes = [ctypes.c_void_p]
        lib.vcs_is_warp.restype = ctypes.c_int
        _lib = lib
        log.info("Loaded Direct3D 11 compositor %s", p)
        return _lib


def available() -> bool:
    return _load() is not None


def load_error() -> str:
    _load()
    return _lib_error


class D3D11Context:
    """One Direct3D 11 device + texture slots. Not thread-safe: the compositor serialises renders."""

    def __init__(self, adapter_index: int = -1):
        lib = _load()
        if lib is None:
            raise RuntimeError(load_error())
        self._lib = lib
        st = ctypes.c_int(0)
        self._h = lib.vcs_create(adapter_index, ctypes.byref(st))
        if not self._h:
            raise RuntimeError("Direct3D 11 device creation failed (no feature level 11_0 device, or shader compile error)")
        buf = ctypes.create_string_buffer(256)
        lib.vcs_device_name(self._h, buf, 256)
        self.device_name = buf.value.decode("utf-8", "replace")
        self.software = bool(lib.vcs_is_warp(self._h))
        self.last_cpu_ms = 0.0
        self.last_gpu_ms = 0.0

    def error(self) -> str:
        buf = ctypes.create_string_buffer(512)
        self._lib.vcs_last_error(self._h, buf, 512)
        return buf.value.decode("utf-8", "replace")

    def upload(self, slot: int, arr: np.ndarray) -> None:
        a = np.ascontiguousarray(arr)
        if a.dtype != np.uint8:
            raise ValueError("uint8 required")
        if a.ndim == 2:
            fmt, stride = 1, a.shape[1]
        elif a.ndim == 3 and a.shape[2] == 4:
            fmt, stride = 0, a.shape[1] * 4
        else:
            raise ValueError("upload expects (H, W) uint8 masks or (H, W, 4) BGRA")
        rc = self._lib.vcs_upload(self._h, slot, a.ctypes.data, a.shape[1], a.shape[0], stride, fmt)
        if rc != 0:
            raise RuntimeError(f"texture upload failed ({rc}): {self.error()}")

    def release(self, slot: int) -> None:
        self._lib.vcs_release_slot(self._h, slot)

    def render(self, src: np.ndarray, dst: np.ndarray, layers: np.ndarray, slots: np.ndarray) -> None:
        if not (src.flags.c_contiguous and dst.flags.c_contiguous):
            raise ValueError("frames must be contiguous")
        h, w = src.shape[:2]
        layers = np.ascontiguousarray(layers, dtype=np.float32)
        slots = np.ascontiguousarray(slots, dtype=np.int32)
        n = len(layers)
        timing = (ctypes.c_double * 2)()
        rc = self._lib.vcs_render(self._h, src.ctypes.data, w, h, src.strides[0], dst.ctypes.data, dst.strides[0],
                                  layers.ctypes.data if n else None, slots.ctypes.data if n else None, n, timing)
        if rc != 0:
            raise RuntimeError(f"Direct3D 11 render failed ({rc}): {self.error()}")
        self.last_cpu_ms, self.last_gpu_ms = timing[0], timing[1]

    def flush(self, drop_slots: bool = False) -> None:
        self._lib.vcs_flush(self._h, 1 if drop_slots else 0)

    def close(self) -> None:
        if self._h:
            self._lib.vcs_destroy(self._h)
            self._h = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


class D3D11Backend(MetalBackend):
    """Same planning-to-layer code as MetalBackend; Direct3D 11 execution on arrays."""
    name = "d3d11"
    BG_RGBA = False                   # D3D11 textures hold BGRA bytes; the shader works in BGRA order
    ctx: D3D11Context  # type: ignore[assignment]  # MetalBackend's ctx is a MetalContext

    def __init__(self):
        self.ctx = D3D11Context()
        if self.ctx.software:
            self.name = "d3d11-warp"     # shown in Replacement/Preview stats: software Direct3D, no GPU found
        self._slots: dict[tuple, int] = {}
        self._slot_use: dict[int, float] = {}
        self._free: list[int] = []
        self._next = 0
        from vcstudio.platform.win.capture import FramePool
        self._out = FramePool()
        log.info("Direct3D 11 compositor on %s", self.ctx.device_name)

    def _cpu_blur_as_layer(self, src: np.ndarray, op: DrawOp) -> DrawOp:
        """WARP only: Gaussian blur through OpenCV, drawn as an opaque composite layer.

        On software Direct3D the shader blur costs ~3x OpenCV's SIMD blur (124 vs 45 ms for sigma 10 over a
        384x756 box on 2 cores). An opaque source with the identity transform makes the composite formula
        k * a + dst * (1 - k) — exactly the effect formula — so the result equals the CPU backend's."""
        from vcstudio.replacement.compositor import CpuBackend
        H, W = src.shape[:2]
        x0, y0, x1, y1 = op.bbox
        eff = CpuBackend._effect(src, op, W, H)                    # float 0..1, already 8-bit rounded
        patch = np.empty((y1 - y0, x1 - x0, 4), np.uint8)
        patch[..., :3] = np.clip(eff * 255 + 0.5, 0, 255).astype(np.uint8)
        patch[..., 3] = 255
        inv = np.array([[1.0, 0.0, -x0], [0.0, 1.0, -y0]])
        return DrawOp(op.roi_id, "composite", op.bbox, op.opacity, mask=op.mask, mask_rect=op.mask_rect,
                      mask_key=op.mask_key, src=patch, src_key=("blurcpu", op.roi_id), inv=inv)

    def execute_pb(self, src_pb, w: int, h: int, ops: list[DrawOp]) -> np.ndarray:
        src = np.ascontiguousarray(src_pb)
        dst = self._out.get(h, w)
        if self.ctx.software:
            ops = [self._cpu_blur_as_layer(src, op) if op.kind == "effect" and op.effect == "blur" else op
                   for op in ops]
        layers, slots, transient = self.build_layers(ops)
        try:
            self.ctx.render(src, dst, layers, slots)
        finally:
            self._free.extend(transient)
        return dst

    def execute(self, frame: np.ndarray, ops: list[DrawOp]) -> np.ndarray:
        h, w = frame.shape[:2]
        return np.array(self.execute_pb(np.ascontiguousarray(frame), w, h, ops))

    def release(self) -> None:
        self.ctx.flush(drop_slots=True)
        self._slots.clear(); self._slot_use.clear(); self._free.clear(); self._next = 0
        from vcstudio.platform.win.capture import FramePool
        self._out = FramePool()
