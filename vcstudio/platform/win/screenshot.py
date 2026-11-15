"""Screenshot of the main display on Windows (GDI BitBlt), for Card for a game calibration.

Captures the primary monitor in physical pixels when the process is DPI aware (Qt makes the app
per-monitor DPI aware), including layered windows (CAPTUREBLT), so a browser or emulator window shows
up as it does on screen. Returns BGR uint8 like cv2.imread, matching the macOS `screencapture` path.
Windows needs no Screen Recording permission; a window that is minimised or covered is simply not in the
picture, which the calibration reports as "markers not found".
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

import numpy as np

SRCCOPY, CAPTUREBLT = 0x00CC0020, 0x40000000
SM_CXSCREEN, SM_CYSCREEN = 0, 1
DIB_RGB_COLORS, BI_RGB = 0, 0


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


def primary_screen() -> np.ndarray:
    """(H, W, 3) uint8 BGR image of the primary monitor."""
    user32, gdi32 = ctypes.WinDLL("user32"), ctypes.WinDLL("gdi32")
    user32.GetDC.restype = wintypes.HDC
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HDC,
                             ctypes.c_int, ctypes.c_int, wintypes.DWORD]
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                                ctypes.c_void_p, wintypes.UINT]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]

    w, h = user32.GetSystemMetrics(SM_CXSCREEN), user32.GetSystemMetrics(SM_CYSCREEN)
    if w <= 0 or h <= 0:
        raise RuntimeError("no display")
    screen = user32.GetDC(None)
    if not screen:
        raise RuntimeError("GetDC failed (no interactive desktop?)")
    mem = gdi32.CreateCompatibleDC(screen)
    bmp = gdi32.CreateCompatibleBitmap(screen, w, h)
    old = gdi32.SelectObject(mem, bmp)
    try:
        if not gdi32.BitBlt(mem, 0, 0, w, h, screen, 0, 0, SRCCOPY | CAPTUREBLT):
            raise RuntimeError(f"BitBlt failed ({ctypes.GetLastError()})")
        bih = _BITMAPINFOHEADER(biSize=ctypes.sizeof(_BITMAPINFOHEADER), biWidth=w, biHeight=-h,   # top-down
                                biPlanes=1, biBitCount=32, biCompression=BI_RGB)
        buf = np.empty((h, w, 4), np.uint8)
        if gdi32.GetDIBits(mem, bmp, 0, h, buf.ctypes.data, ctypes.byref(bih), DIB_RGB_COLORS) != h:
            raise RuntimeError("GetDIBits failed")
        return np.ascontiguousarray(buf[..., :3])
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, screen)
