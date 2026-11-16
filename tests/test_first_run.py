"""First-run wizard: shown once, applies language / source / mode / autostart, never needs admin rights."""
from __future__ import annotations

import pytest


@pytest.fixture
def ctx(paths):
    from vcstudio.app.application import AppContext
    c = AppContext(paths, configure_logging=False)
    yield c
    c.shutdown()


INFO = {"windows": "Windows 11 Pro", "gpus": [], "providers": ["DML", "CPU"], "camera_installed": True,
        "camera_visible": True, "obs_version": "33.0.0", "obs_plugin": True, "cameras": ["USB Camera"]}


def _wizard(ctx, qtbot, monkeypatch, info=INFO, measured=None):
    from vcstudio.ai import auto_profile
    from vcstudio.ui.first_run import FirstRunWizard
    monkeypatch.setattr(auto_profile, "measure", lambda models: measured or
                        {"model": "yolox_nano", "backend": "CPU", "median_ms": 50.0, "mode": "low_resource"})
    w = FirstRunWizard(ctx.settings, ctx.models, info)
    qtbot.addWidget(w)
    w.show()
    return w


def test_wizard_applies_the_choices(ctx, qtbot, monkeypatch):
    from vcstudio import i18n
    w = _wizard(ctx, qtbot, monkeypatch)
    try:
        lang = w.pages[0]
        lang.combo.setCurrentIndex(lang.combo.findData("ru"))
        w.next()                                                  # -> this PC
        assert "Видеокарта" in w.pages[1].text.text() or "Graphics card" in w.pages[1].text.text()
        assert "USB Camera" in w.pages[1].text.text()
        w.next()                                                  # -> source: OBS + plugin preselected
        src = w.pages[2]
        assert src.obs.isChecked()
        src.cam.setChecked(True)
        w.next()                                                  # -> mode: measured in the background
        mode = w.pages[3]
        qtbot.waitUntil(lambda: mode.result is not None, timeout=5000)
        assert mode.combo.currentData() == "low_resource"
        w.next()                                                  # -> done
        w.pages[4].autostart.setChecked(False)
        w.accept()
        s = ctx.settings
        assert s.get("ui.language") == "ru" and s.get("input.method") == "directshow"
        assert s.get("input.device_name_hint") == "USB Camera"
        assert s.get("performance.mode") == "low_resource" and s.get("performance.auto_mode_done")
        assert s.get("input.autostart") is False and s.get("ui.first_run_done") is True
    finally:
        i18n.set_language("en")


def test_skipping_marks_it_done_and_changes_nothing_else(ctx, qtbot, monkeypatch):
    before = (ctx.settings.get("input.method"), ctx.settings.get("performance.mode"))
    w = _wizard(ctx, qtbot, monkeypatch)
    w.reject()                                                    # "Skip setup"
    assert ctx.settings.get("ui.first_run_done") is True
    assert (ctx.settings.get("input.method"), ctx.settings.get("performance.mode")) == before
    from vcstudio.ui.first_run import run_if_needed
    assert run_if_needed(ctx.settings, ctx.models) is False     # not again


def test_without_obs_or_cameras_the_test_pattern_is_offered(ctx, qtbot, monkeypatch):
    info = dict(INFO, obs_version=None, obs_plugin=False, cameras=[])
    w = _wizard(ctx, qtbot, monkeypatch, info)
    w.next(); w.next()
    src = w.pages[2]
    assert not src.obs.isEnabled() and not src.cam.isEnabled() and src.test.isChecked()
    w.reject()


def test_system_summary_probes_this_pc():
    from vcstudio.ui.first_run import system_summary
    s = system_summary()
    assert "windows" in s and isinstance(s["cameras"], list) and "camera_installed" in s
