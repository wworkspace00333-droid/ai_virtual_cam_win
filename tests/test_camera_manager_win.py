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


def test_install_uninstall_and_rename_run_the_setup_tool_elevated(cm):
    assert cm.activate() == (0, "") and cm.deactivate() == (0, "")
    assert cm.rename("  Studio cam  ") == (0, "")
    assert cm.calls == ["--install", "--uninstall", '--rename "Studio cam"']


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
    (["not_installed"], False, "vcam-setup.exe", "click Install", False),
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


def test_setup_tool_missing_is_reported_without_a_uac_prompt(monkeypatch):
    from vcstudio.camera import camera_manager
    from vcstudio.platform.win import vcam_status as vs
    monkeypatch.setattr(vs, "setup_tool", lambda: None)
    code, msg = camera_manager._run_setup_elevated("--install")
    assert code == 1 and "vcam-setup.exe not found" in msg
