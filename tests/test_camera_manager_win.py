"""Windows camera manager: status messages for every registration state and the elevated setup calls (no UAC here:
vcam-setup is replaced by a recorder)."""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows camera manager")


@pytest.fixture
def cm(monkeypatch):
    from vcstudio.camera import camera_manager
    calls: list[str] = []
    monkeypatch.setattr(camera_manager, "_run_setup_elevated", lambda flag, timeout=120: (calls.append(flag), (0, ""))[1])
    camera_manager.calls = calls
    return camera_manager


def test_install_uninstall_reinstall_and_rename_run_the_setup_tool_elevated(cm):
    assert cm.activate() == (0, "") and cm.deactivate() == (0, "") and cm.reinstall() == (0, "")
    assert cm.rename("  Studio cam  ") == (0, "")
    assert cm.calls == ["--install", "--uninstall", "--reinstall", '--rename "Studio cam"']


def _installed_setup_tool():
    """vcam-setup.exe next to the registered DirectShow DLL (the installed copy), or None."""
    import winreg
    from pathlib import Path
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"CLSID\{59E4F394-D9A7-48BF-8D06-8DB7D8CF1ECE}\InprocServer32",
                            0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            dll = Path(winreg.QueryValueEx(k, "")[0])
    except OSError:
        return None
    exe = dll.with_name("vcam-setup.exe")
    return exe if exe.exists() else None


def _camera_registry_keys() -> list[str]:
    """Registry keys the camera creates (COM classes, DirectShow filter entry) that must be gone after uninstall."""
    import winreg
    found = []
    for clsid in ("{59E4F394-D9A7-48BF-8D06-8DB7D8CF1ECE}", "{20C0F518-9F28-4168-9F73-030F243FB3B9}"):
        for root, base in ((winreg.HKEY_CLASSES_ROOT, rf"CLSID\{clsid}"),
                           (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Classes\CLSID\{clsid}"),
                           (winreg.HKEY_CLASSES_ROOT,
                            rf"CLSID\{{860BB310-5D01-11d0-BD3B-00A0C911CE86}}\Instance\{clsid}")):  # video input category
            try:
                winreg.CloseKey(winreg.OpenKey(root, base, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY))
                found.append(base)
            except OSError:
                pass
    return found


@pytest.mark.skipif(__import__("os").environ.get("VCS_LIVE_UNINSTALL") != "1",
                    reason="set VCS_LIVE_UNINSTALL=1 (removes and re-registers the installed camera; elevated shell)")
def test_uninstall_leaves_no_registry_entries_and_reinstall_restores_both_cameras():
    import subprocess
    exe = _installed_setup_tool()
    if exe is None:
        pytest.skip("no installed camera to remove")
    assert _camera_registry_keys()                                    # sanity: something is registered now
    try:
        r = subprocess.run([str(exe), "--uninstall"], capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stdout + r.stderr
        left = _camera_registry_keys()
        assert left == [], f"left behind after uninstall: {left}"
        from vcstudio.core.devices import is_own_output, list_video_devices
        assert not any(is_own_output(d.name, "") for d in list_video_devices())   # no device left in the lists
    finally:
        r = subprocess.run([str(exe), "--install", "--both"], capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(_camera_registry_keys()) >= 3                          # both classes back (+ the filter entry)


@pytest.mark.parametrize("name,why", [("", "1-63"), ("x" * 64, "1-63"), ("bad\x07name", "1-63"), ('say "hi"', "quotation")])
def test_rename_rejects_bad_names_before_asking_for_admin_rights(cm, name, why):
    code, msg = cm.rename(name)
    assert code == 1 and why in msg and cm.calls == []


def _comp(state, name="DirectShow camera", path="C:/x.dll"):
    return SimpleNamespace(state=state, name=name, path=path)


@pytest.mark.parametrize("states,visible,tool,expect,ready", [
    (["installed"], True, "vcam-setup.exe", "installed and visible", True),
    (["installed"], False, "vcam-setup.exe", "not listed as a video device yet", False),
    (["broken_registration"], False, "vcam-setup.exe", "file is missing", False),
    (["not_installed"], False, None, "built and registered by the Windows installer", False),
    (["not_installed"], False, "vcam-setup.exe", "Not installed: the camera components are here", False),
])
def test_status_message_for_each_registration_state(monkeypatch, states, visible, tool, expect, ready):
    from vcstudio.camera import camera_manager
    from vcstudio.core import devices
    from vcstudio.platform.win import vcam_status as vs
    monkeypatch.setattr(vs, "components", lambda: [_comp(s) for s in states])
    monkeypatch.setattr(vs, "setup_tool", lambda: tool)
    monkeypatch.setattr(devices, "list_video_devices",
                        lambda: [SimpleNamespace(name="AI Virtual Camera")] if visible else [])
    st = camera_manager.status()
    assert expect in st.message and st.ready is ready
    assert st.extension_state == ("installed" if "installed" in states else
                                  "broken_registration" if "broken_registration" in states else "not_installed")


def test_status_when_the_device_list_fails(monkeypatch):
    from vcstudio.camera import camera_manager
    from vcstudio.core import devices
    from vcstudio.platform.win import vcam_status as vs
    monkeypatch.setattr(vs, "components", lambda: [_comp("installed")])
    monkeypatch.setattr(vs, "setup_tool", lambda: "vcam-setup.exe")

    def boom():
        raise OSError("enumeration failed")
    monkeypatch.setattr(devices, "list_video_devices", boom)
    st = camera_manager.status()
    assert not st.device_visible and "not listed as a video device yet" in st.message


class _FakeWinApi:
    """Stands in for shell32 / kernel32: records the ShellExecuteExW request, returns a chosen outcome."""

    def __init__(self, started=True, error=0, wait=0, exit_code=0):
        self.started, self.error, self.wait, self.exit_code = started, error, wait, exit_code
        self.request = None
        self.closed = []
        self.shell32 = SimpleNamespace(ShellExecuteExW=self._shell_execute)
        self.kernel32 = SimpleNamespace(WaitForSingleObject=self._wait, GetExitCodeProcess=self._exit_code,
                                        CloseHandle=self.closed.append)

    def _shell_execute(self, ref):
        info = ref._obj
        self.request = (info.lpVerb, info.lpFile, info.lpParameters, info.nShow, info.fMask)
        info.hProcess = 1234
        return 1 if self.started else 0

    def _wait(self, handle, ms):
        self.wait_ms = ms
        return self.wait

    def _exit_code(self, handle, ref):
        ref._obj.value = self.exit_code
        return 1

    def api(self):
        return self.shell32, self.kernel32, lambda: self.error


@pytest.fixture
def elevated(monkeypatch, tmp_path):
    from vcstudio.camera import camera_manager
    from vcstudio.platform.win import vcam_status as vs
    tool = tmp_path / "vcam-setup.exe"
    monkeypatch.setattr(vs, "setup_tool", lambda: tool)

    def use(**kw):
        fake = _FakeWinApi(**kw)
        monkeypatch.setattr(camera_manager, "_win_api", fake.api)
        return fake, tool
    return camera_manager, use


def test_elevated_setup_runs_hidden_with_runas_and_returns_its_exit_code(elevated):
    cm, use = elevated
    fake, tool = use(exit_code=3)
    assert cm._run_setup_elevated("--install", timeout=2.5) == (3, "")
    assert fake.request == ("runas", str(tool), "--install", 0, 0x40)
    assert fake.wait_ms == 2500 and fake.closed == [1234]                 # process handle closed


def test_elevated_setup_declined_or_not_started(elevated):
    cm, use = elevated
    use(started=False, error=1223)
    assert cm._run_setup_elevated("--install") == (1, "Administrator rights were declined.")
    use(started=False, error=2)
    assert cm._run_setup_elevated("--install") == (1, "Could not start vcam-setup.exe (2).")


def test_elevated_setup_timeout_still_closes_the_handle(elevated):
    cm, use = elevated
    fake, _ = use(wait=0x102)                                              # WAIT_TIMEOUT
    assert cm._run_setup_elevated("--uninstall", timeout=1) == (1, "vcam-setup.exe did not finish in time.")
    assert fake.closed == [1234]


def test_win_api_points_at_the_system_libraries():
    import ctypes

    from vcstudio.camera import camera_manager
    shell32, kernel32, last_error = camera_manager._win_api()
    assert shell32 is ctypes.windll.shell32 and kernel32 is ctypes.windll.kernel32 and last_error is ctypes.GetLastError


def test_setup_tool_missing_is_reported_without_a_uac_prompt(monkeypatch):
    from vcstudio.camera import camera_manager
    from vcstudio.platform.win import vcam_status as vs
    monkeypatch.setattr(vs, "setup_tool", lambda: None)
    code, msg = camera_manager._run_setup_elevated("--install")
    assert code == 1 and "vcam-setup.exe not found" in msg
