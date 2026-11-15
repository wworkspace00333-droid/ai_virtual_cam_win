"""Platform selection: the one place that knows which OS the app runs on.

macOS code (PyObjC, Metal, CMIO) and Windows code (Media Foundation, ONNX Runtime, Direct3D 11,
the DirectShow / Media Foundation virtual camera) live side by side. Shared modules ask this
module which implementation to use instead of testing sys.platform themselves.

`vcstudio.platform.win` holds the Windows implementations. The macOS implementations stay where
they always were (the original modules), so the macOS build is unchanged.
"""
from __future__ import annotations

import contextlib
import sys

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
NAME = "windows" if IS_WINDOWS else ("macos" if IS_MAC else sys.platform)


def autorelease_pool():
    """Context manager that drains Objective-C temporaries on macOS; a no-op elsewhere.

    Worker loops (capture, AI, pipeline, video decode) wrap every iteration in it: on macOS plain
    Python threads have no autorelease pool, elsewhere there is nothing to drain."""
    if IS_MAC:
        try:
            import objc  # type: ignore
            return objc.autorelease_pool()
        except ImportError:
            pass
    return contextlib.nullcontext()


def native_lib_name(stem: str) -> str:
    """File name of a native library built from this repository: libX.dylib on macOS, X.dll on Windows."""
    return f"{stem}.dll" if IS_WINDOWS else f"lib{stem}.dylib"
