"""Windows camera privacy switches (Settings → Privacy & security → Camera).

Windows keeps the user's choices in the CapabilityAccessManager consent store:
  HKLM ...\\ConsentStore\\webcam              Value = Allow | Deny   (device-wide "Camera access", admin/policy)
  HKCU ...\\ConsentStore\\webcam              Value = Allow | Deny   ("Let apps access your camera")
  HKCU ...\\ConsentStore\\webcam\\NonPackaged Value = Allow | Deny   ("Let desktop apps access your camera")
A desktop (non-packaged) app such as this one can open a camera only when all three allow it.
A missing key means the default, which is Allow. Group Policy (LetAppsAccessCamera = 2) forces Deny.

Mapped onto the app's existing states: authorized | denied | restricted.
"""
from __future__ import annotations

import winreg

CONSENT = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam"
POLICY = r"SOFTWARE\Policies\Microsoft\Windows\AppPrivacy"


def _value(root, path: str, name: str = "Value"):
    try:
        with winreg.OpenKey(root, path) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def camera_access() -> dict:
    """Each switch as True / False / None (not set = default Allow)."""
    def allow(v):
        return None if v is None else str(v).lower() == "allow"
    return {
        "device": allow(_value(winreg.HKEY_LOCAL_MACHINE, CONSENT)),
        "apps": allow(_value(winreg.HKEY_CURRENT_USER, CONSENT)),
        "desktop_apps": allow(_value(winreg.HKEY_CURRENT_USER, CONSENT + r"\NonPackaged")),
        "policy_force_deny": _value(winreg.HKEY_LOCAL_MACHINE, POLICY, "LetAppsAccessCamera") == 2,
    }


def camera_authorization_status() -> str:
    """Windows 11 greys out "Let desktop apps access your camera" when the master "Let apps access your
    camera" switch is off, so both must allow. On Windows 10 the master switch covers Store apps only and
    desktop apps follow their own switch."""
    from vcstudio.platform.win.sysinfo import is_windows_11
    a = camera_access()
    if a["policy_force_deny"]:
        return "restricted"
    if a["device"] is False or a["desktop_apps"] is False:
        return "denied"
    if a["apps"] is False and is_windows_11():
        return "denied"
    return "authorized"
