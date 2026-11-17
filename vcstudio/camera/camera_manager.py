"""Virtual camera installation status and control (host app + CMIO system extension)."""
from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from vcstudio import CAMERA_NAME
from vcstudio.core.system_info import camera_extensions

log = logging.getLogger(__name__)
EXTENSION_ID = "com.aivcstudio.app.camera"
DEFAULT_HOST_APP = Path("/Applications/AI Virtual Camera Studio.app")
APPROVAL_PANE = "x-apple.systempreferences:com.apple.LoginItems-Settings.extension"


@dataclass
class CameraStatus:
    host_app: str | None          # path of the signed host app, if found
    extension_state: str          # not_installed | activated_enabled | waiting_for_user | ... (systemextensionsctl)
    extension_enabled: bool
    extension_active: bool
    device_visible: bool          # "AI Virtual Camera" present in AVFoundation's device list
    team_id: str = ""
    message: str = ""

    @property
    def ready(self) -> bool:
        return self.extension_enabled and self.extension_active and self.device_visible


def host_app() -> Path | None:  # pragma: macos
    env = os.environ.get("VCSTUDIO_HOST_APP")
    for p in ([Path(env)] if env else []) + [DEFAULT_HOST_APP]:
        if (p / "Contents" / "MacOS" / "AIVirtualCameraStudio").exists():
            return p
    return None


def _status_windows() -> CameraStatus:
    """Windows: DirectShow + Media Foundation COM servers (vcstudio/platform/win/vcam_status.py)."""
    from vcstudio.platform.win import vcam_status as vs
    comps = vs.components()
    try:
        from vcstudio.core.devices import is_own_output, list_video_devices
        visible = any(is_own_output(d.name, "") for d in list_video_devices())   # incl. "(Windows Virtual Camera)"
    except Exception:
        visible = False
    installed = [c for c in comps if c.state == "installed"]
    broken = [c for c in comps if c.state == "broken_registration"]
    tool = vs.setup_tool()
    name = vs.camera_name()
    st = CameraStatus(str(tool) if tool else None,
                      "installed" if installed else ("broken_registration" if broken else "not_installed"),
                      bool(installed), bool(installed), visible)
    if installed and visible:
        st.message = f"{name} is installed and visible to other apps."
    elif installed:
        st.message = (f"{name} is registered ({', '.join(c.name for c in installed)}) but not listed as a "
                      "video device yet. Restart the app that should use it.")
    elif broken:
        st.message = (f"{name} is registered but its file is missing ({broken[0].path}). Reinstall the app.")
    elif tool is None:
        st.message = (f"Not installed. The {name} components (native/win) are built and registered by the "
                      "Windows installer; they are not present in this build yet.")
    else:
        st.message = "Not installed: the camera components are here but not registered. Click Install (administrator rights needed)."
    return st


def status() -> CameraStatus:
    from vcstudio.platform import IS_WINDOWS
    if IS_WINDOWS:
        return _status_windows()
    return _status_mac()  # pragma: macos


def _status_mac() -> CameraStatus:  # pragma: macos
    ext = next((e for e in camera_extensions() if e["bundle_id"] == EXTENSION_ID), None)
    try:
        from vcstudio.core.devices import list_video_devices
        visible = any(d.name == CAMERA_NAME for d in list_video_devices())
    except Exception:
        visible = False
    app = host_app()
    st = CameraStatus(str(app) if app else None, (ext or {}).get("state", "not_installed").replace(" ", "_"),
                      bool(ext and ext["enabled"]), bool(ext and ext["active"]), visible, (ext or {}).get("team_id", ""))
    if st.ready:
        st.message = f"{CAMERA_NAME} is installed and visible to other apps."
    elif ext and not ext["enabled"]:
        st.message = ("Waiting for approval: System Settings > General > Login Items & Extensions > Camera "
                      f"Extensions > enable “{CAMERA_NAME}”.")
    elif ext:
        st.message = f"Extension state: {ext['state']}. A restart may be required."
    elif app is None:
        st.message = ("Not installed. Build and install the signed app with scripts/build_camera.sh "
                      "(needs Xcode and an Apple Developer team; see docs/CAMERA_SETUP.md).")
    else:
        st.message = "Host app found but the camera extension is not activated yet: click Install."
    return st


def _run_host(flag: str, timeout: float = 150) -> tuple[int, str]:  # pragma: macos
    app = host_app()
    if app is None:
        return 1, "Signed host app not found in /Applications (run scripts/build_camera.sh)."
    try:
        p = subprocess.run([str(app / "Contents" / "MacOS" / "AIVirtualCameraStudio"), flag],
                           capture_output=True, text=True, timeout=timeout)
        out = (p.stdout + p.stderr).strip()
        log.info("%s -> exit %s: %s", flag, p.returncode, out)
        return p.returncode, out
    except subprocess.TimeoutExpired:
        return 1, "Timed out waiting for the system extension request."


def _run_setup_elevated(flag: str, timeout: float = 120) -> tuple[int, str]:
    """Windows: run vcam-setup.exe with administrator rights (UAC prompt) and wait for its exit code."""
    import ctypes
    from ctypes import wintypes

    from vcstudio.platform.win import vcam_status as vs
    tool = vs.setup_tool()
    if tool is None:
        return 1, "vcam-setup.exe not found: this build does not contain the Windows camera components."

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                    ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]
    SEE_MASK_NOCLOSEPROCESS = 0x40
    info = SHELLEXECUTEINFOW(cbSize=ctypes.sizeof(SHELLEXECUTEINFOW), fMask=SEE_MASK_NOCLOSEPROCESS,
                             lpVerb="runas", lpFile=str(tool), lpParameters=flag, nShow=0)
    shell32, k32, last_error = _win_api()
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        err = last_error()
        return 1, ("Administrator rights were declined." if err == 1223 else f"Could not start vcam-setup.exe ({err}).")
    try:
        if k32.WaitForSingleObject(info.hProcess, int(timeout * 1000)) != 0:
            return 1, "vcam-setup.exe did not finish in time."
        code = wintypes.DWORD()
        k32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        log.info("vcam-setup %s -> exit %s", flag, code.value)
        return int(code.value), ""
    finally:
        k32.CloseHandle(info.hProcess)


def _win_api():
    """(shell32, kernel32, GetLastError): one place, so the tests can stand in for the UAC prompt."""
    import ctypes
    return ctypes.windll.shell32, ctypes.windll.kernel32, ctypes.GetLastError


def activate() -> tuple[int, str]:
    """0 active, 2 needs user approval, 3 needs reboot, other = error."""
    from vcstudio.platform import IS_WINDOWS
    if IS_WINDOWS:
        return _run_setup_elevated("--install")
    return _run_host("--activate-camera")  # pragma: macos


def reinstall() -> tuple[int, str]:
    """Windows: repair the camera: remove both devices and register the same set again (one UAC prompt).
    Exit codes as for install: 0 ok, 3 the DirectShow camera replaced a Media Foundation camera that would not start."""
    from vcstudio.platform import IS_WINDOWS
    if not IS_WINDOWS:
        return 1, "Reinstalling the camera this way is available on Windows."
    return _run_setup_elevated("--reinstall", timeout=180)


def rename(name: str) -> tuple[int, str]:
    """Windows: give the camera devices a new name (re-registers what is installed; administrator rights).
    Windows 11 shows the Media Foundation camera as "<name> (Windows Virtual Camera)"."""
    from vcstudio.platform import IS_WINDOWS
    if not IS_WINDOWS:
        return 1, "Renaming the camera is available on Windows."
    from vcstudio.platform.win.vcam_status import valid_camera_name
    name = name.strip()
    if not valid_camera_name(name):
        return 1, "The name must be 1-63 characters without control characters."
    if '"' in name:
        return 1, "The name cannot contain quotation marks."
    return _run_setup_elevated(f'--rename "{name}"')


def deactivate() -> tuple[int, str]:
    from vcstudio.platform import IS_WINDOWS
    if IS_WINDOWS:
        return _run_setup_elevated("--uninstall")
    return _run_host("--deactivate-camera")  # pragma: macos


def open_approval_settings() -> None:
    """macOS: Camera Extensions approval pane. Windows: Settings → Privacy & security → Camera."""
    from vcstudio.platform import IS_WINDOWS
    if IS_WINDOWS:
        os.startfile("ms-settings:privacy-webcam")      # noqa: S606 - fixed system URI
        return
    subprocess.Popen(["open", APPROVAL_PANE])  # pragma: macos
