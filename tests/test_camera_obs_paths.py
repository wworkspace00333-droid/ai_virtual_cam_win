"""Windows paths that CI can run without a camera, OBS or administrator rights (D11: coverage in CI): the camera
test-pattern sender, the camera status messages and setup argument checks, the OBS plugin install/uninstall into a
temporary ProgramData."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows paths")


class FakeOutput:
    def __init__(self, accept_every=1):
        self.opened = self.closed = False
        self.frames = []
        self.accept_every = accept_every
        self.n = 0

    def open(self):
        self.opened = True

    def send(self, buf, pts):
        self.n += 1
        if self.n % self.accept_every:
            return False
        self.frames.append(buf)
        return True

    def close(self):
        self.closed = True


def test_test_pattern_sender_sends_numbered_frames():
    from vcstudio.camera.test_sender import TestPatternSender
    out = FakeOutput(accept_every=2)
    s = TestPatternSender(out, 320, 180, fps=60)
    s.start()
    try:
        end = time.monotonic() + 5
        while time.monotonic() < end and s.sent < 6:
            time.sleep(0.02)
        assert s.running and out.opened
    finally:
        s.stop()
    assert out.closed and not s.running and s.error == ""
    assert s.sent >= 6 and s.dropped >= s.sent - 1                 # every 2nd frame refused by the fake camera
    f = out.frames[0]
    assert f.shape == (180, 320, 4) and f[..., 3].min() == 255
    assert tuple(f[10, 5, :3]) == (255, 255, 255)                  # first colour bar: white


def test_test_pattern_sender_reports_a_failing_camera():
    from vcstudio.camera.test_sender import TestPatternSender

    class Broken(FakeOutput):
        def send(self, buf, pts):
            raise OSError("device gone")
    s = TestPatternSender(Broken(), 160, 90, fps=30)
    s.start()
    end = time.monotonic() + 3
    while time.monotonic() < end and s.running:
        time.sleep(0.02)
    s.stop()
    assert "device gone" in s.error


@pytest.mark.parametrize("case, expect", [
    ("installed_visible", "installed and visible"),
    ("installed_hidden", "not listed as a video device"),
    ("broken", "file is missing"),
    ("no_tool", "not present in this build"),
    ("not_registered", "Click Install"),
])
def test_camera_status_messages(monkeypatch, case, expect):
    from vcstudio.camera import camera_manager as cm
    from vcstudio.core import devices
    from vcstudio.platform.win import vcam_status as vs
    comp = {"installed_visible": (True, True), "installed_hidden": (True, True), "broken": (True, False),
            "no_tool": (False, False), "not_registered": (False, False)}[case]
    monkeypatch.setattr(vs, "components", lambda: [vs.ComponentStatus("DirectShow camera", comp[0], r"C:\x.dll",
                                                                      comp[1])])
    monkeypatch.setattr(vs, "setup_tool", lambda: None if case == "no_tool" else Path(r"C:\vcam-setup.exe"))
    monkeypatch.setattr(vs, "camera_name", lambda: "AI Virtual Camera")

    class Dev:
        name = "AI Virtual Camera"
    monkeypatch.setattr(devices, "list_video_devices", lambda: [Dev()] if case == "installed_visible" else [])
    st = cm.status()
    assert expect in st.message
    assert st.extension_state == {"installed_visible": "installed", "installed_hidden": "installed",
                                  "broken": "broken_registration"}.get(case, "not_installed")
    assert st.ready is (case == "installed_visible")


def test_camera_setup_without_the_tool_and_rename_checks(monkeypatch):
    from vcstudio.camera import camera_manager as cm
    from vcstudio.platform.win import vcam_status as vs
    monkeypatch.setattr(vs, "setup_tool", lambda: None)
    for fn in (cm.activate, cm.reinstall, cm.deactivate):
        code, msg = fn()
        assert code == 1 and "vcam-setup.exe not found" in msg          # no UAC prompt without the tool
    assert cm.rename("   ")[0] == 1 and "1-63" in cm.rename("")[1]
    assert "quotation" in cm.rename('My "cam"')[1]
    assert cm.rename("x" * 64)[0] == 1
    assert "not found" in cm.rename("Studio Cam")[1]                    # valid name: reaches the (missing) tool


def test_obs_plugin_install_and_uninstall(monkeypatch, tmp_path):
    from vcstudio.core import obs_plugin as op
    from vcstudio.core import resources
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    built = tmp_path / "build" / "vcstudio-obs.dll"
    monkeypatch.setattr(resources, "obs_plugin_bundle", lambda: built)
    monkeypatch.setattr(op, "obs_running", lambda: False)
    assert op.built_plugin() is None and op.installed_plugin() is None
    with pytest.raises(RuntimeError, match="not built"):
        op.install()
    built.parent.mkdir(parents=True)
    built.write_bytes(b"MZ plugin")
    msg = op.install()
    dest = tmp_path / "pd" / "obs-studio" / "plugins" / "vcstudio-obs" / "bin" / "64bit" / "vcstudio-obs.dll"
    assert dest.read_bytes() == b"MZ plugin" and str(dest) in msg and "Start OBS" in msg
    assert op.installed_plugin() == dest
    monkeypatch.setattr(op, "obs_running", lambda: True)
    assert "Restart OBS" in op.install()
    op.uninstall()
    assert op.installed_plugin() is None and not dest.parent.exists()
    op.uninstall()                                                      # nothing there: no error


def test_obs_plugin_install_without_rights(monkeypatch, tmp_path):
    import shutil

    from vcstudio.core import obs_plugin as op
    from vcstudio.core import resources
    built = tmp_path / "vcstudio-obs.dll"
    built.write_bytes(b"MZ")
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    monkeypatch.setattr(resources, "obs_plugin_bundle", lambda: built)

    def denied(*a, **k):
        raise PermissionError("denied")
    monkeypatch.setattr(shutil, "copy2", denied)
    with pytest.raises(RuntimeError, match="No permission"):
        op.install()

    def locked(*a, **k):
        raise OSError("in use")
    monkeypatch.setattr(shutil, "copy2", locked)
    monkeypatch.setattr(op, "obs_running", lambda: True)
    with pytest.raises(RuntimeError, match="close OBS"):
        op.install()
    monkeypatch.setattr(op, "obs_running", lambda: False)
    with pytest.raises(OSError, match="in use"):
        op.install()


def test_obs_detection_helpers(monkeypatch, tmp_path):
    from vcstudio.core import obs_plugin as op
    assert op._file_version(Path(sys.executable)) is not None          # python.exe carries a version resource
    assert op._file_version(tmp_path / "missing.exe") is None
    fake = tmp_path / "obs-studio"
    (fake / "bin" / "64bit").mkdir(parents=True)
    import winreg

    def no_key(*a, **k):
        raise OSError("no key")
    monkeypatch.setattr(winreg, "OpenKey", no_key)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    assert op.obs_install_dir() is None                                 # no obs64.exe yet
    (fake / "bin" / "64bit" / "obs64.exe").write_bytes(b"MZ")
    assert op.obs_install_dir() == fake
    assert op.obs_version() is None                                     # a file without a version resource
    assert isinstance(op.obs_running(), bool)
