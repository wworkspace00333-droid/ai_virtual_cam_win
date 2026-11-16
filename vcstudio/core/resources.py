"""Locate bundled resources in both layouts: the source tree (development) and the packaged app.

Packaged (PyInstaller, sys.frozen): everything lives under sys._MEIPASS:
    native/libVCSNative.dylib, native/libVCSShm.dylib
    models/<name>.mlpackage + .json
    sample_data/street.jpg, logo_alpha.png, clip_h264.mp4
    ../Resources/obs-plugin/vcstudio-obs.plugin   (copied in by scripts/build_app.sh)
    scripts/                      (developer tools runnable with --run-script)
Development: the same files at their repository locations (build/native, models, tests/data, ...).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", REPO))


def native_lib(name: str) -> Path:
    return bundle_root() / "native" / name if frozen() else REPO / "build" / "native" / name


def models_dir() -> Path:
    return bundle_root() / "models" if frozen() else REPO / "models"


# Sample photo with people and buses (self-check, model import trial, benchmark scene). Public domain (CC0):
# "Moscow, Paveletskaya Square, people at bus stop Sept 2025 02.jpg", Wikimedia Commons, 1280 px rendition
# (THIRD_PARTY_NOTICES.md). It replaced Ultralytics' bus.jpg, which is AGPL-licensed.
SAMPLE_PHOTO = "street.jpg"


def sample_photo() -> Path:
    return sample(SAMPLE_PHOTO)


def sample(name: str) -> Path:
    return bundle_root() / "sample_data" / name if frozen() else REPO / "tests" / "data" / name


def app_icon_path() -> Path | None:
    """Window / tray icon (packaging/AppIcon.ico; bundled at the root of the frozen app)."""
    p = bundle_root() / "AppIcon.ico" if frozen() else REPO / "packaging" / "AppIcon.ico"
    return p if p.exists() else None


def camera_test_page() -> Path:
    """Local browser page for checking the virtual camera (Virtual Camera page, Windows)."""
    return bundle_root() / "vcstudio" / "camera" / "camera_test.html"


def accuracy_data() -> Path:
    """16 labelled COCO images used by the accuracy check (tests/data/coco16)."""
    return bundle_root() / "sample_data" / "coco16" if frozen() else REPO / "tests" / "data" / "coco16"


def obs_plugin_bundle() -> Path:
    if sys.platform == "win32":     # vcstudio-obs.dll (scripts/build_obs_plugin_win.ps1)
        return (bundle_root() / "obs-plugin" / "vcstudio-obs.dll" if frozen()
                else REPO / "build" / "obs-plugin" / "win" / "vcstudio-obs.dll")
    # packaged: Contents/Resources/obs-plugin/ (sys._MEIPASS is Contents/Frameworks)
    return (bundle_root().parent / "Resources" / "obs-plugin" / "vcstudio-obs.plugin" if frozen()
            else REPO / "build" / "obs-plugin" / "vcstudio-obs.plugin")


def scripts_dir() -> Path:
    return bundle_root() / "scripts" if frozen() else REPO / "scripts"
