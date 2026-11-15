"""Minimal COM helpers over ctypes (no comtypes/pywin32 dependency, works in a frozen app).

COM objects are plain pointers; methods are called by vtable index. Indices used by callers are
documented next to each call and come from the Windows SDK headers (IUnknown = 0..2).
"""
from __future__ import annotations

import contextlib
import ctypes
import uuid
from ctypes import wintypes

ole32 = ctypes.WinDLL("ole32")
oleaut32 = ctypes.WinDLL("oleaut32")
ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
ole32.CoCreateInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p]
oleaut32.SysFreeString.argtypes = [ctypes.c_void_p]
oleaut32.VariantClear.argtypes = [ctypes.c_void_p]

COINIT_MULTITHREADED = 0x0
CLSCTX_INPROC_SERVER = 0x1
S_OK, S_FALSE = 0, 1
RPC_E_CHANGED_MODE = -2147417850


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def of(cls, s: str) -> "GUID":
        u = uuid.UUID(s.strip("{}"))
        g = cls()
        g.Data1, g.Data2, g.Data3 = u.fields[0], u.fields[1], u.fields[2]
        g.Data4[:] = list(u.bytes[8:])
        return g

    def __str__(self) -> str:
        b = bytes(self.Data4)
        return "{%08X-%04X-%04X-%s-%s}" % (self.Data1, self.Data2, self.Data3, b[:2].hex().upper(), b[2:].hex().upper())

    def __eq__(self, other) -> bool:
        return isinstance(other, GUID) and bytes(self) == bytes(other)

    def __hash__(self) -> int:
        return hash(bytes(self))


def fourcc_guid(code: str) -> GUID:
    """Media subtype GUID for a FourCC (XXXXXXXX-0000-0010-8000-00AA00389B71)."""
    d1 = int.from_bytes(code.encode("ascii"), "little")
    return GUID.of(f"{d1:08X}-0000-0010-8000-00AA00389B71")


def subtype_name(g: GUID) -> str:
    """Readable name of a video subtype GUID (FourCC, or RGB32/RGB24 for the D3DFORMAT ones)."""
    if str(g).endswith("-0000-0010-8000-00AA00389B71"):
        named = {0x16: "RGB32", 0x15: "ARGB32", 0x14: "RGB24", 0x17: "RGB565"}
        if g.Data1 in named:
            return named[g.Data1]
        try:
            s = g.Data1.to_bytes(4, "little").decode("ascii")
            if s.isprintable():
                return s.strip()
        except UnicodeDecodeError:
            pass
    return str(g)


def vcall(obj, index: int, restype, *argtypes):
    """Bound function at vtable `index` of COM object `obj` (call it with obj as the first argument)."""
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtbl[index])


def release(obj) -> None:
    if obj:
        vcall(obj, 2, wintypes.ULONG)(obj)


@contextlib.contextmanager
def com_object(ptr: ctypes.c_void_p):
    """Release a COM pointer at the end of the block."""
    try:
        yield ptr
    finally:
        release(ptr)


COINIT_APARTMENTTHREADED = 0x2


def init_thread(sta: bool = False) -> bool:
    """CoInitializeEx for the calling thread. Returns True if this call must be balanced by CoUninitialize.
    A thread that is already initialised keeps its apartment.

    Measured with OBS Virtual Camera 33 (ThreadingModel=Both): CoCreateInstance(IBaseFilter) succeeds on
    an MTA thread and returns E_NOINTERFACE on a fresh STA thread, so enumeration uses MTA."""
    hr = ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED if sta else COINIT_MULTITHREADED)
    return hr in (S_OK, S_FALSE)


def uninit_thread() -> None:
    ole32.CoUninitialize()


class VARIANT(ctypes.Structure):
    _fields_ = [("vt", wintypes.USHORT), ("r1", wintypes.WORD), ("r2", wintypes.WORD), ("r3", wintypes.WORD),
                ("p", ctypes.c_void_p), ("p2", ctypes.c_void_p)]


VT_BSTR = 8
