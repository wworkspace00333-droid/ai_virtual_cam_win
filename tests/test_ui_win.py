"""Windows GUI and configuration: settings migration, hotkeys, camera status, platform texts."""
import ctypes
import json
import sys
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows GUI layer")


def test_mac_settings_are_repaired_to_windows_defaults(paths):
    """A settings.json copied from a Mac keeps everything valid and falls back where macOS-only values were used."""
    from vcstudio.core.config import Settings
    paths.settings_file.write_text(json.dumps({"input": {"method": "avfoundation", "preferred_fps": 30},
                                               "processing": {"compositor": "metal"}}), encoding="utf-8")
    s = Settings(paths)
    problems = s.load()
    assert any("input.method" in p for p in problems) and any("processing.compositor" in p for p in problems)
    assert s.get("input.method") == "directshow"
    assert s.get("processing.compositor") == "auto"
    assert s.get("input.preferred_fps") == 30                 # valid values are kept
    for m in ("mediafoundation", "directshow", "obs_ipc", "test_pattern"):
        s.set("input.method", m)
    s.set("processing.compositor", "d3d11")


def test_platform_texts_and_choices():
    from vcstudio import platform as p
    from vcstudio.i18n_ru import RU
    assert p.DEFAULT_DEVICE_METHOD == "directshow" and p.GPU_COMPOSITOR == "d3d11"
    for key in p._TEXT:
        mac, win = p._TEXT[key]
        assert p.text(key) == win
        assert win in RU, f"missing Russian translation for {key}"
    for _, label in p.INPUT_METHODS + p.COMPOSITORS:
        assert label in RU


def test_hotkey_parsing():
    from vcstudio.platform.win.hotkeys import MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, parse
    assert parse("Ctrl+Alt+S") == (ord("S"), MOD_CONTROL | MOD_ALT)
    assert parse("Ctrl+Alt+Right") == (0x27, MOD_CONTROL | MOD_ALT)
    assert parse("Shift+Meta+F5") == (0x74, MOD_SHIFT | MOD_WIN)
    assert parse("Ctrl+PgDown")[0] == 0x22
    assert parse("S") is None                   # never a plain key system-wide
    assert parse("Hyper+S") is None and parse("") is None


def test_global_hotkey_registers_and_fires():
    """Register a real system-wide hotkey, press it with SendInput, receive the action."""
    from vcstudio.platform.win.hotkeys import GlobalHotkeys
    hk = GlobalHotkeys()
    fired = threading.Event()
    got = []
    try:
        errors = hk.apply({"probe": "Ctrl+Alt+Shift+F24", "bad": "Q"}, lambda a: (got.append(a), fired.set()))
        assert "bad" in errors and "probe" not in errors and hk.count == 1
        # second registration of the same combination from another owner is refused by Windows
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        assert not user32.RegisterHotKey(None, 99, 0x2 | 0x1 | 0x4, 0x87)
        # press Ctrl+Alt+Shift+F24 (VK 0x87)
        KEYUP = 0x2
        for vk in (0x11, 0x12, 0x10, 0x87):
            user32.keybd_event(vk, 0, 0, 0)
        for vk in (0x87, 0x10, 0x12, 0x11):
            user32.keybd_event(vk, 0, KEYUP, 0)
        if not fired.wait(2):
            pytest.skip("synthetic key input is not delivered in this (non-interactive) session")
        assert got == ["probe"]
    finally:
        hk.clear()
        assert hk.count == 0
        hk.close()


def test_camera_status_is_honest_when_not_installed():
    from vcstudio.camera import camera_manager as cm
    from vcstudio.platform.win import vcam_status as vs
    s = cm.status()
    comps = vs.components()
    assert len(comps) == 2
    if not any(c.registered for c in comps):
        assert s.extension_state == "not_installed" and not s.ready
        assert "Not installed" in s.message


def test_camera_privacy_status_is_known():
    from vcstudio.core.devices import camera_authorization_status
    assert camera_authorization_status() in ("authorized", "denied", "restricted")


def test_camera_device_capture_reports_clearly_until_available():
    from vcstudio.core.video_capture import CaptureError, device_source
    try:
        src = device_source("directshow", "dshow-clsid:{00000000-0000-0000-0000-000000000001}", "No Such Camera")
    except CaptureError as e:
        assert "not found" in str(e)
    else:
        assert src.kind in ("mediafoundation", "directshow")


def test_window_starts_and_hotkey_signal_reaches_gui_thread(qtbot, paths, monkeypatch):
    from vcstudio.app.application import AppContext
    from vcstudio.ui.main_window import MainWindow
    ctx = AppContext(paths, configure_logging=False)
    w = MainWindow(ctx)
    qtbot.addWidget(w)
    try:
        seen = []
        monkeypatch.setattr(w, "on_action", lambda a: seen.append((a, threading.current_thread() is threading.main_thread())))
        w.global_hotkey_fired.disconnect()
        w.global_hotkey_fired.connect(w.on_action)
        t = threading.Thread(target=lambda: w.global_hotkey_fired.emit("toggle_ai"))
        t.start(); t.join()
        qtbot.waitUntil(lambda: bool(seen), timeout=2000)
        assert seen == [("toggle_ai", True)]
    finally:
        w.close()
        ctx.shutdown()
