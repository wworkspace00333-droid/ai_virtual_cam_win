"""Installation status of this app's Windows virtual camera ("AI Virtual Camera").

Two COM servers make up the camera (native/win, porting stage 8):
  DirectShow source filter  vcam_dshow.dll  registered under CLSID_DSHOW and in the
                            "Video Capture Sources" filter category -> Windows 10 and 11
  Media Foundation source   vcam_mf.dll     CLSID_MF, registered with MFCreateVirtualCamera -> Windows 11
The installer registers them (administrator rights) through vcam-setup.exe. This module only reads
the registry and the device list; it never claims the camera works because a file exists.
"""
from __future__ import annotations

import winreg
from dataclasses import dataclass
from pathlib import Path

CLSID_DSHOW = "{59E4F394-D9A7-48BF-8D06-8DB7D8CF1ECE}"
CLSID_MF = "{20C0F518-9F28-4168-9F73-030F243FB3B9}"
CLSID_VIDEO_INPUT_CATEGORY = "{860BB310-5D01-11D0-BD3B-00A0C911CE86}"   # CLSID_VideoInputDeviceCategory


def _server_path(clsid: str) -> str | None:
    """InprocServer32 path of a registered COM class (64-bit view), or None."""
    for view in (winreg.KEY_WOW64_64KEY, 0):
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"CLSID\{clsid}\InprocServer32", 0,
                                winreg.KEY_READ | view) as k:
                return str(winreg.QueryValueEx(k, "")[0])
        except OSError:
            continue
    return None


def _in_video_category(clsid: str) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT,
                            rf"CLSID\{CLSID_VIDEO_INPUT_CATEGORY}\Instance\{clsid}", 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY):
            return True
    except OSError:
        return False


@dataclass
class ComponentStatus:
    name: str
    registered: bool
    path: str | None
    file_exists: bool

    @property
    def state(self) -> str:
        if not self.registered:
            return "not_installed"
        return "installed" if self.file_exists else "broken_registration"


def components() -> list[ComponentStatus]:
    out = []
    p = _server_path(CLSID_DSHOW)
    out.append(ComponentStatus("DirectShow camera (Windows 10/11)", p is not None and _in_video_category(CLSID_DSHOW),
                               p, bool(p and Path(p).exists())))
    p = _server_path(CLSID_MF)
    out.append(ComponentStatus("Media Foundation camera (Windows 11)", p is not None, p, bool(p and Path(p).exists())))
    return out


def setup_tool() -> Path | None:
    """vcam-setup.exe next to the app (packaged) or in the native build folder (development)."""
    from vcstudio.core.resources import bundle_root, frozen, REPO
    for p in ((bundle_root() / "native" / "vcam-setup.exe") if frozen() else None,
              REPO / "build" / "native" / "vcam-setup.exe"):
        if p is not None and p.exists():
            return p
    return None
