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


# ------------------------------------------------------------------------------ capabilities
# Capture methods that open a camera device ("input.method"). Labels are English source strings (tr()).
if IS_WINDOWS:
    # DirectShow first: OBS Virtual Camera (and most virtual cameras) are DirectShow filters and do not
    # appear in Media Foundation (checked with OBS 33). Physical webcams appear in both.
    DEVICE_METHODS: tuple[tuple[str, str], ...] = (
        ("directshow", "Camera device (DirectShow): OBS Virtual Camera"),
        ("mediafoundation", "Camera device (Media Foundation): webcams"),
    )
else:
    DEVICE_METHODS = (("avfoundation", "Camera device (AVFoundation): OBS Virtual Camera"),)
DEVICE_METHOD_KEYS = frozenset(k for k, _ in DEVICE_METHODS)
DEFAULT_DEVICE_METHOD = DEVICE_METHODS[0][0]
INPUT_METHODS = DEVICE_METHODS + (("obs_ipc", "Direct OBS frame transfer (vcstudio-obs plugin)"),
                                  ("test_pattern", "Test pattern (diagnostics)"))


def is_device_method(method: str | None) -> bool:
    return method in DEVICE_METHOD_KEYS


# GPU compositor of this platform ("processing.compositor"): auto | <gpu> | cpu
GPU_COMPOSITOR = "d3d11" if IS_WINDOWS else "metal"
GPU_COMPOSITOR_LABEL = "Direct3D 11 GPU" if IS_WINDOWS else "Metal GPU"
COMPOSITORS: tuple[tuple[str, str], ...] = (
    ("auto", f"Automatic ({GPU_COMPOSITOR_LABEL} if available)"),
    (GPU_COMPOSITOR, GPU_COMPOSITOR_LABEL),
    ("cpu", "CPU (NumPy/OpenCV fallback)"),
)

# User-visible sentences that differ per OS (English source strings; callers pass them through tr()).
_TEXT = {
    "camera_permission_not_determined": (
        "macOS will ask for camera permission when capture starts.",
        "Windows asks apps for camera access in Settings → Privacy & security → Camera."),
    "camera_permission_denied": (
        "Denied. Enable it in System Settings → Privacy & Security → Camera, then restart the app.",
        "Denied. Turn on “Camera access” and “Let desktop apps access your camera” in Settings → "
        "Privacy & security → Camera, then restart capture."),
    "language_auto": ("Automatic (macOS language)", "Automatic (Windows display language)"),
    "hotkeys_note": (
        "With “System-wide” on, shortcuts work even when another app is in front (Carbon hotkeys; no "
        "Accessibility permission needed). On macOS, “Ctrl” in these fields is the ⌘ key. A shortcut another app "
        "already owns is reported and falls back to an in-app shortcut.",
        "With “System-wide” on, shortcuts work even when another app is in front (Windows RegisterHotKey; no "
        "administrator rights needed). A shortcut another app already owns is reported and falls back to an "
        "in-app shortcut."),
    "memory_auto": ("Automatic: {mb} MB for this Mac's {gb} GB", "Automatic: {mb} MB for this PC's {gb} GB"),
    "paste_hint": ("drop photos here or paste with ⌘V", "drop photos here or paste with Ctrl+V"),
    "calibration_not_found": (
        "The game's camera picture was not found on screen ({e}). Make sure the game is visible on the main "
        "display, shows this camera, and that Screen Recording is allowed for this app (System Settings > "
        "Privacy & Security).",
        "The game's camera picture was not found on screen ({e}). Make sure the game is visible on the main "
        "display (not minimised or covered by another window) and shows this camera."),
    "performance_note": (
        "Operating mode and live resource usage. Mode FPS values are targets; measured results on this Mac are in "
        "docs/BENCHMARK.md (scripts/benchmark.py).",
        "Operating mode and live resource usage. Mode FPS values are targets; measure this PC with "
        "scripts/benchmark.py."),
    "another_copy_running": (
        "AI Virtual Camera Studio is already running (process {pid}). Two copies would fight over the output "
        "camera, so this one will close. Switch to the open window in the Dock.",
        "AI Virtual Camera Studio is already running (process {pid}). Two copies would fight over the output "
        "camera, so this one will close. Switch to the open window in the taskbar."),
    "camera_page_intro": (
        "Where processed video goes. “{camera}” is this app's own CoreMediaIO camera extension; OBS Virtual "
        "Camera works without an Apple Developer account.",
        "Where processed video goes. “{camera}” is this app's own Windows camera: a DirectShow camera for "
        "Windows 10/11 and a Media Foundation camera for Windows 11, registered by the installer."),
    "camera_chain_hint": (
        "One click: the app pauses its own processing, sends a 1080p60 test scene and then your OBS video through "
        "AI and Metal into the selected camera, receives it like QuickTime or Zoom would, and shows frame rate, "
        "dropped frames, latency and whether the processing is visible. About 30 seconds.",
        "One click: the app pauses its own processing, sends a 1080p60 test scene and then your OBS video through "
        "AI and the compositor into the selected camera, receives it like Zoom or the Camera app would, and shows "
        "frame rate, dropped frames, latency and whether the processing is visible. About 30 seconds."),
    "camera_verify_hint": (
        "Send a moving, numbered test pattern to the selected output camera, then select that camera in FaceTime, "
        "QuickTime Player (File > New Movie Recording), Photo Booth or Zoom. The loopback test captures the camera "
        "like a client app and measures delivered frames and latency.",
        "Send a moving, numbered test pattern to the selected output camera, then select that camera in the Camera "
        "app, Zoom, Microsoft Teams, Google Chrome or Discord. The loopback test captures the camera like a client "
        "app and measures delivered frames and latency."),
    "camera_working": ("Working… macOS may ask you to approve the extension.",
                       "Working… Windows asks for administrator rights to register the camera."),
    "camera_remove_question": ("Remove “{camera}” from this Mac?", "Remove “{camera}” from this PC?"),
    "camera_needs_approval": (
        "Approve it in System Settings > General > Login Items & Extensions > Camera Extensions.",
        "Allow camera access in Settings > Privacy & security > Camera."),
    "camera_needs_restart": ("Restart the Mac to finish.", "Restart Windows to finish."),
}

# Monospace font for logs and code-like text
MONO_FONT = "Consolas" if IS_WINDOWS else "Menlo"


def text(key: str) -> str:
    """English source string for this OS (pass the result through i18n.tr)."""
    mac, win = _TEXT[key]
    return win if IS_WINDOWS else mac
