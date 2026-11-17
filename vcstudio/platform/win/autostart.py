"""Start with Windows (current user only): HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run.

Off by default; the Settings page turns it on or off. The entry starts the app in the tray (--minimized). No
administrator rights, nothing for other users; the uninstaller removes the value (installer.iss).
"""
from __future__ import annotations

import sys
import winreg
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "AIVirtualCameraStudio"


def command() -> str:
    """The command line Windows runs at sign-in."""
    if getattr(sys, "frozen", False):                     # installed app: vcstudio.exe
        return f'"{sys.executable}" --minimized'
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")                     # source tree: no console window
    return f'"{pyw if pyw.exists() else exe}" -m vcstudio --minimized'


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, VALUE)
            return True
    except OSError:
        return False


def current() -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return str(winreg.QueryValueEx(k, VALUE)[0])
    except OSError:
        return None


def _exe_of(cmd: str) -> str:
    cmd = cmd.strip()
    if cmd.startswith('"'):
        return cmd[1:].split('"', 1)[0]
    return cmd.split(" ", 1)[0]


def repair(value: str = VALUE) -> str:
    """At start of the installed app: an entry that points to an exe that no longer exists or to another install
    folder (uninstalled by another administrator — the uninstaller can only clean its own user's HKCU — then
    installed again, maybe elsewhere) is rewritten to this exe. Returns "" (nothing to do), "updated"."""
    if not getattr(sys, "frozen", False):
        return ""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            cmd = str(winreg.QueryValueEx(k, value)[0])
    except OSError:
        return ""
    exe = _exe_of(cmd)
    if exe and Path(exe).exists() and Path(exe).resolve() == Path(sys.executable).resolve():
        return ""
    set_enabled(True, value)
    return "updated"


def set_enabled(on: bool, value: str = VALUE) -> None:
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as k:
        if on:
            winreg.SetValueEx(k, value, 0, winreg.REG_SZ, command())
        else:
            try:
                winreg.DeleteValue(k, value)
            except FileNotFoundError:
                pass
