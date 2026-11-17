"""Tray (window hides, the camera keeps running), start with Windows (HKCU Run), diagnostics ZIP without personal data."""
from __future__ import annotations

import json
import sys
import zipfile

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows")


def test_autostart_entry_is_added_and_removed_for_the_current_user(monkeypatch):
    from vcstudio.platform.win import autostart
    name = "AIVirtualCameraStudio-test"
    monkeypatch.setattr(autostart, "VALUE", name)
    try:
        autostart.set_enabled(True, name)
        assert autostart.is_enabled() and "--minimized" in autostart.current()
        assert "vcstudio" in autostart.current().lower()
    finally:
        autostart.set_enabled(False, name)
    assert not autostart.is_enabled()


def test_autostart_entry_to_a_removed_exe_is_repaired(monkeypatch, tmp_path):
    """D13: uninstalled by another administrator, installed again (elsewhere): the stale entry follows the new exe."""
    import sys

    from vcstudio.platform.win import autostart
    name = "AIVirtualCameraStudio-test-repair"
    monkeypatch.setattr(autostart, "VALUE", name)
    exe = tmp_path / "new" / "vcstudio.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"MZ")
    import winreg
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, autostart.RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, f'"{tmp_path / "gone" / "vcstudio.exe"}" --minimized')
        assert autostart.repair(name) == ""                        # source tree (not frozen): never touched
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(exe))
        assert autostart.repair(name) == "updated"
        assert autostart.current() == f'"{exe}" --minimized'
        assert autostart.repair(name) == ""                        # now correct: nothing to do
    finally:
        autostart.set_enabled(False, name)
    assert autostart.repair(name) == ""                            # no entry: nothing created


def test_redactor_replaces_user_computer_home_and_email(tmp_path):
    import getpass
    import socket
    from pathlib import Path

    from vcstudio.app.diagnostics import Redactor
    r = Redactor(tmp_path)
    home = str(Path.home())
    text = (f"user {getpass.getuser()} on {socket.gethostname()} path {home}\\Videos\\clip.mp4 "
            f"data {tmp_path}\\settings.json mail someone@example.com")
    out = r(text)
    assert getpass.getuser().lower() not in out.lower() or len(getpass.getuser()) < 2
    assert socket.gethostname().lower() not in out.lower()
    assert home.lower() not in out.lower() and str(tmp_path).lower() not in out.lower()
    assert "<home>\\Videos\\clip.mp4" in out and "<data>\\settings.json" in out and "<email>" in out


def test_diagnostics_zip_has_the_parts_and_no_personal_data(paths, tmp_path):
    import getpass
    import socket

    from vcstudio.app.application import AppContext
    from vcstudio.app.diagnostics import write_zip
    ctx = AppContext(paths, configure_logging=False)
    try:
        ctx.db.log_event("ERROR", "test", f"something failed in {paths.root} for {getpass.getuser()}")
        out = tmp_path / "diag.zip"
        info = write_zip(ctx, out, include_selfcheck=False)
    finally:
        ctx.shutdown()
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        assert {"system.json", "camera.json", "settings.json", "events.json", "README.txt"} <= names
        assert any(n.startswith("profiles/") for n in names)
        blob = "\n".join(z.read(n).decode("utf-8", "replace") for n in names)
        json.loads(z.read("system.json"))
    assert info["files"] == len(names)
    for secret in (str(paths.root), socket.gethostname()):
        assert secret.lower() not in blob.lower(), secret
    assert "<data>" in blob


def test_close_hides_to_tray_and_quit_really_exits(qtbot, paths, monkeypatch):
    from vcstudio.app.application import AppContext
    from vcstudio.ui import tray as tray_mod
    from vcstudio.ui.main_window import MainWindow
    monkeypatch.setattr(tray_mod.AppTray, "available", staticmethod(lambda: True))
    quits = []
    monkeypatch.setattr("PySide6.QtWidgets.QApplication.quit", lambda: quits.append(1))
    ctx = AppContext(paths, configure_logging=False)
    w = MainWindow(ctx)
    qtbot.addWidget(w)
    try:
        assert w.tray is not None
        w.show()
        ctx.settings.set("ui.close_to_tray", True)
        w.close()
        assert not w.isVisible() and quits == []          # hidden to the tray, the app goes on
        w.tray.sync()
        assert w.tray.a_show.text()                        # menu texts follow the state
        w.tray.toggle_window()
        assert w.isVisible()
        w.quit_app()
        assert not w.isVisible() and quits                 # "Quit" closes for real
    finally:
        ctx.shutdown()
