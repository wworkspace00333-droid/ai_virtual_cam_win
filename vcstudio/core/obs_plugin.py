"""Locate / install / inspect the vcstudio-obs OBS plugin (input method 2).

macOS:   ~/Library/Application Support/obs-studio/plugins/vcstudio-obs.plugin (bundle)
Windows: %ProgramData%\\obs-studio\\plugins\\vcstudio-obs\\bin\\64bit\\vcstudio-obs.dll (the plugin folder
         OBS 28+ scans for third-party plugins). Writing there may need administrator rights; the Windows
         installer places the plugin, and the Input page can (re)install it when the folder is writable.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from vcstudio.platform import IS_WINDOWS

PLUGIN_NAME = "vcstudio-obs.plugin"
OBS_APP = Path("/Applications/OBS.app")
INSTALL_DIR = Path.home() / "Library" / "Application Support" / "obs-studio" / "plugins"

WIN_MODULE = "vcstudio-obs"


def _win_install_dir() -> Path:
    return Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "obs-studio" / "plugins" / WIN_MODULE


def _win_dll(folder: Path) -> Path:
    return folder / "bin" / "64bit" / f"{WIN_MODULE}.dll"


def built_plugin() -> Path | None:
    from vcstudio.core.resources import obs_plugin_bundle
    p = obs_plugin_bundle()
    if IS_WINDOWS:
        return p if p.is_file() else None
    return p if (p / "Contents" / "MacOS" / "vcstudio-obs").exists() else None


def installed_plugin() -> Path | None:
    if IS_WINDOWS:
        p = _win_dll(_win_install_dir())
        return p if p.is_file() else None
    p = INSTALL_DIR / PLUGIN_NAME
    return p if (p / "Contents" / "MacOS" / "vcstudio-obs").exists() else None


def obs_install_dir() -> Path | None:
    """Windows: OBS's installation folder from the registry (HKLM\\SOFTWARE\\OBS Studio), or the default."""
    import winreg
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\OBS Studio", 0, winreg.KEY_READ | view) as k:
                p = Path(str(winreg.QueryValueEx(k, "")[0]))
                if (p / "bin" / "64bit" / "obs64.exe").exists():
                    return p
        except OSError:
            continue
    p = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "obs-studio"
    return p if (p / "bin" / "64bit" / "obs64.exe").exists() else None


def _file_version(path: Path) -> str | None:
    import ctypes
    from ctypes import wintypes
    ver = ctypes.WinDLL("version")
    size = ver.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return None
    buf = ctypes.create_string_buffer(size)
    if not ver.GetFileVersionInfoW(str(path), 0, size, buf):
        return None
    p, n = ctypes.c_void_p(), wintypes.UINT()
    if not ver.VerQueryValueW(buf, "\\", ctypes.byref(p), ctypes.byref(n)) or not p:
        return None
    words = (ctypes.c_uint32 * 13).from_address(int(p.value or 0))     # VS_FIXEDFILEINFO (p checked non-null)
    ms, ls = words[4], words[5]                                  # dwProductVersionMS / LS
    return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}"


def obs_version() -> str | None:
    if IS_WINDOWS:
        d = obs_install_dir()
        return _file_version(d / "bin" / "64bit" / "obs64.exe") if d else None
    try:
        out = subprocess.run(["defaults", "read", str(OBS_APP / "Contents" / "Info.plist"), "CFBundleShortVersionString"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        return out or None
    except (OSError, subprocess.SubprocessError):
        return None


def obs_running() -> bool:
    if IS_WINDOWS:
        import psutil
        return any((p.info.get("name") or "").lower() == "obs64.exe" for p in psutil.process_iter(["name"]))
    return subprocess.run(["pgrep", "-x", "OBS"], capture_output=True).returncode == 0


def install() -> str:
    """Copy the plugin into OBS's plugin folder. Returns a user-facing message."""
    src = built_plugin()
    if src is None:
        raise RuntimeError("Plugin not built: run scripts/build_obs_plugin_win.ps1" if IS_WINDOWS
                           else "Plugin not built: run scripts/build_obs_plugin.sh")
    if IS_WINDOWS:
        dest = _win_dll(_win_install_dir())
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        except PermissionError:
            raise RuntimeError(f"No permission to write {dest.parent}. Run the AI Virtual Camera Studio installer "
                               "(it installs the OBS plugin) or start the app once as administrator.") from None
        except OSError as e:
            if obs_running():
                raise RuntimeError("OBS is running and has the plugin loaded: close OBS, then install again.") from e
            raise
    else:
        INSTALL_DIR.mkdir(parents=True, exist_ok=True)
        dest = INSTALL_DIR / PLUGIN_NAME
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
    msg = f"Installed to {dest}."
    msg += (" Restart OBS, then enable Tools > AI Virtual Camera Studio: share frames." if obs_running()
            else " Start OBS, then enable Tools > AI Virtual Camera Studio: share frames.")
    return msg


def uninstall() -> None:
    if IS_WINDOWS:
        d = _win_install_dir()
        if d.exists():
            shutil.rmtree(d)
        return
    dest = INSTALL_DIR / PLUGIN_NAME
    if dest.exists():
        shutil.rmtree(dest)
