"""Writer side of the app -> "AI Virtual Camera" frame transport (native/win/vcam/vcam_shm.h).

Creates "Local\\AIVirtualCamera.Frames" in the user's session with a security descriptor that lets the
camera readers open it, and nobody else:
  this user: full access (the DirectShow camera runs inside this user's apps);
  LOCAL SERVICE (the Frame Server, which runs the Media Foundation camera): read;
  app containers and restricted (sandboxed) processes: read — both are checked twice by Windows, so they
  still need to run as this user;
  integrity label Low with no-write-up, so low-integrity client processes can still read.
Not Everyone: the section can be opened from other sessions as \\Sessions\\<id>\\BaseNamedObjects\\..., and
"Everyone: read" let another user of the same PC watch this user's camera output.
If the name already exists, it must belong to this user (another copy of the app or a test writer); a section
pre-created by someone else is refused instead of written into.
Publishing follows the seqlock protocol; heartbeat and timestamps use QueryPerformanceCounter in ns
(time.perf_counter_ns), the clock the camera DLLs read with QueryPerformanceCounter.
"""
from __future__ import annotations

import ctypes
import os
import threading
import time
from ctypes import wintypes

import numpy as np

NAME = "Local\\AIVirtualCamera.Frames"
MAGIC, VERSION, SLOTS, HEADER = 0x4D414356, 1, 3, 4096
MAX_W, MAX_H = 1920, 1080
SLOT_BYTES = MAX_W * MAX_H * 4
TOTAL = HEADER + SLOTS * SLOT_BYTES
# header offsets (vcam_shm_header)
O_MAGIC, O_VERSION, O_SLOTS, O_HEADER, O_SLOT_BYTES, O_LATEST, O_HEARTBEAT = 0, 4, 8, 12, 16, 24, 32
O_PID, O_FPS_NUM, O_FPS_DEN, O_ACTIVE, O_WIDTH, O_HEIGHT, O_SLOT0, SLOT_SIZE = 40, 44, 48, 52, 56, 60, 72, 32
ERROR_ALREADY_EXISTS = 183


def sddl_for(user_sid: str) -> str:
    return f"D:P(A;;GA;;;{user_sid})(A;;GR;;;LS)(A;;GR;;;AC)(A;;GR;;;RC)S:(ML;;NW;;;LW)"


_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_adv = ctypes.WinDLL("advapi32", use_last_error=True)
_k32.GetCurrentProcess.restype = wintypes.HANDLE
_adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
_adv.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                     ctypes.POINTER(wintypes.DWORD)]
_adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
_adv.GetSecurityInfo.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
                                 ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.c_void_p,
                                 ctypes.POINTER(ctypes.c_void_p)]
_adv.GetSecurityInfo.restype = wintypes.DWORD


def _sid_str(psid) -> str:
    s = wintypes.LPWSTR()
    if not _adv.ConvertSidToStringSidW(psid, ctypes.byref(s)):
        raise OSError(ctypes.get_last_error(), "ConvertSidToStringSid")
    try:
        return s.value or ""
    finally:
        _k32.LocalFree(s)


def token_sids() -> tuple[str, str]:
    """(user SID, default owner SID) of this process; the owner is Administrators when the app runs elevated."""
    tok = wintypes.HANDLE()
    if not _adv.OpenProcessToken(_k32.GetCurrentProcess(), 0x0008, ctypes.byref(tok)):     # TOKEN_QUERY
        raise OSError(ctypes.get_last_error(), "OpenProcessToken")
    try:
        out = []
        for cls in (1, 4):                                                                 # TokenUser, TokenOwner
            n = wintypes.DWORD()
            _adv.GetTokenInformation(tok, cls, None, 0, ctypes.byref(n))
            buf = ctypes.create_string_buffer(n.value)
            if not _adv.GetTokenInformation(tok, cls, buf, n, ctypes.byref(n)):
                raise OSError(ctypes.get_last_error(), "GetTokenInformation")
            out.append(_sid_str(ctypes.c_void_p.from_buffer(buf).value))   # first field: PSID
        return out[0], out[1]
    finally:
        _k32.CloseHandle(tok)


def object_owner(handle) -> str:
    owner, sd = ctypes.c_void_p(), ctypes.c_void_p()
    err = _adv.GetSecurityInfo(handle, 6, 0x1, ctypes.byref(owner), None, None, None, ctypes.byref(sd))  # KERNEL_OBJECT, OWNER
    if err:
        raise OSError(err, "GetSecurityInfo")
    try:
        return _sid_str(owner)
    finally:
        _k32.LocalFree(sd)
_k32.CreateFileMappingW.restype = wintypes.HANDLE
_k32.CreateFileMappingW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                    wintypes.LPCWSTR]
_k32.MapViewOfFile.restype = ctypes.c_void_p
_k32.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
_k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.LocalFree.argtypes = [ctypes.c_void_p]
_adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.ULONG)]


class SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p), ("bInheritHandle", wintypes.BOOL)]


class VcamWriter:
    def __init__(self, name: str = NAME):
        user, owner = token_sids()
        sd = ctypes.c_void_p()
        if not _adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl_for(user), 1, ctypes.byref(sd), None):
            raise OSError(ctypes.get_last_error(), "security descriptor")
        try:
            sa = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), sd, False)
            self.h = _k32.CreateFileMappingW(wintypes.HANDLE(-1), ctypes.byref(sa), 0x04, TOTAL >> 32, TOTAL & 0xFFFFFFFF, name)
            err = ctypes.get_last_error()
        finally:
            _k32.LocalFree(sd)
        if not self.h:
            raise OSError(err, f"CreateFileMapping({name}) failed")
        if err == ERROR_ALREADY_EXISTS:
            try:
                found = object_owner(self.h)
            except OSError:
                found = "?"
            if found not in (user, owner):
                _k32.CloseHandle(self.h)
                self.h = None
                raise PermissionError(f"{name} already exists and belongs to {found}, not to this user; refusing to "
                                      "send camera frames into it")
        self.base = _k32.MapViewOfFile(self.h, 0x0002 | 0x0004, 0, 0, 0)          # FILE_MAP_WRITE | READ
        if not self.base:
            err = ctypes.get_last_error()
            _k32.CloseHandle(self.h)
            raise OSError(err, "MapViewOfFile failed")
        self._u32(O_MAGIC, MAGIC); self._u32(O_VERSION, VERSION); self._u32(O_SLOTS, SLOTS)
        self._u32(O_HEADER, HEADER); self._u64(O_SLOT_BYTES, SLOT_BYTES); self._u32(O_PID, os.getpid())
        self.n = self._get64(O_LATEST)          # continue numbering if a camera kept the section alive
        self.sent = 0
        # publish / configure / close from different threads (the pipeline sends while the UI closes the output, the
        # test-pattern sender shares this writer): close must never unmap the view under a frame being written -
        # that was an access violation (D10). After close, publish and configure do nothing.
        self._lock = threading.Lock()

    def _u32(self, off: int, v: int) -> None:
        ctypes.c_uint32.from_address(self.base + off).value = v

    def _u64(self, off: int, v: int) -> None:
        ctypes.c_uint64.from_address(self.base + off).value = v

    def _get64(self, off: int) -> int:
        return ctypes.c_uint64.from_address(self.base + off).value

    def configure(self, width: int, height: int, fps: float) -> None:
        with self._lock:
            if not self.base:
                return
            self._configure(width, height, fps)

    def _configure(self, width: int, height: int, fps: float) -> None:
        self._u32(O_WIDTH, width); self._u32(O_HEIGHT, height)
        self._u32(O_FPS_NUM, int(round(fps * 1000))); self._u32(O_FPS_DEN, 1000)
        self._u32(O_ACTIVE, 1)

    def publish(self, bgra: np.ndarray, ts_ns: int | None = None) -> None:
        h, w = bgra.shape[:2]
        if w > MAX_W or h > MAX_H or bgra.ndim != 3 or bgra.shape[2] != 4 or bgra.dtype != np.uint8:
            raise ValueError(f"camera frames are BGRA uint8 up to {MAX_W}x{MAX_H}, got {bgra.shape} {bgra.dtype}")
        with self._lock:
            if self.base:
                self._publish(bgra, h, w, ts_ns)

    def _publish(self, bgra: np.ndarray, h: int, w: int, ts_ns: int | None) -> None:
        n = self.n + 1
        i = n % SLOTS
        so = O_SLOT0 + i * SLOT_SIZE
        self._u64(so, 2 * n - 1)
        dst = np.ctypeslib.as_array((ctypes.c_uint8 * (h * w * 4)).from_address(self.base + HEADER + i * SLOT_BYTES))
        np.copyto(dst.reshape(h, w, 4), bgra)
        self._u32(so + 8, w); self._u32(so + 12, h); self._u32(so + 16, w * 4); self._u32(so + 20, 1)
        self._u64(so + 24, ts_ns if ts_ns is not None else time.perf_counter_ns())
        self._u64(so, 2 * n)
        self._u64(O_LATEST, n)
        self._u64(O_HEARTBEAT, time.perf_counter_ns())
        self.n = n
        self.sent += 1

    def close(self) -> None:
        lock = getattr(self, "_lock", None)   # absent if __init__ failed before mapping
        if lock is None:
            self._close()
            return
        with lock:
            self._close()

    def _close(self) -> None:
        if getattr(self, "base", None):
            # only the current writer marks the output closed: a second, short-lived writer on the same section
            # (a test, the test-pattern sender of another copy) must not blank the camera of the one still running
            if ctypes.c_int32.from_address(self.base + O_PID).value == os.getpid():
                self._u32(O_ACTIVE, 0)
            _k32.UnmapViewOfFile(self.base)
            self.base = None
        if getattr(self, "h", None):
            _k32.CloseHandle(self.h)
            self.h = None
