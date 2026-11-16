"""First start: the performance mode is chosen from a measurement, once, and never over the user's own choice."""
from __future__ import annotations

import threading

import pytest

from vcstudio.ai import auto_profile as ap


@pytest.mark.parametrize("ms,mode", [(5, "performance"), (12, "performance"), (12.1, "balanced"), (35, "balanced"),
                                     (35.5, "low_resource"), (300, "low_resource")])
def test_mode_thresholds(ms, mode):
    assert ap.mode_for(ms) == mode


def test_measure_runs_the_smallest_builtin_detector(tmp_path):
    from vcstudio.ai.model_manager import ModelManager
    mm = ModelManager(tmp_path)
    if not mm.ranked("detect"):
        pytest.skip("no detector model")
    r = ap.measure(mm)
    assert r["model"] == mm.ranked("detect")[-1].name and r["median_ms"] > 0
    assert r["mode"] == ap.mode_for(r["median_ms"]) and r["backend"]


@pytest.fixture
def ctx(paths):
    from vcstudio.app.application import AppContext
    c = AppContext(paths, configure_logging=False)
    yield c
    c.shutdown()


def _choose(ctx, monkeypatch, result_mode="low_resource", user_changes_to=None):
    monkeypatch.setattr(ap, "measure", lambda models: (
        ctx.settings.set("performance.mode", user_changes_to) if user_changes_to else None,
        {"model": "m", "backend": "CPU", "median_ms": 99.0, "mode": result_mode})[1])
    got = threading.Event()
    box = []
    t = ap.choose_once(ctx.settings, ctx.models, lambda r: (box.append(r), got.set()))
    assert t is not None and got.wait(5)
    return ap.apply_result(ctx, box[0])


def test_first_start_applies_the_measured_mode_to_settings_and_profile(ctx, monkeypatch):
    assert ctx.settings.get("performance.mode") == "balanced" and not ctx.settings.get("performance.auto_mode_done")
    assert _choose(ctx, monkeypatch) is True
    assert ctx.settings.get("performance.mode") == "low_resource"
    assert ctx.profiles.active()["body"]["performance_mode"] == "low_resource"
    assert ctx.settings.get("performance.auto_mode_done") and "99.0 ms" in ctx.settings.get("performance.auto_mode_note")
    assert ap.choose_once(ctx.settings, ctx.models, lambda r: None) is None      # only once


def test_a_mode_picked_by_the_user_meanwhile_is_kept(ctx, monkeypatch):
    assert _choose(ctx, monkeypatch, user_changes_to="performance") is False
    assert ctx.settings.get("performance.mode") == "performance"
    assert ctx.settings.get("performance.auto_mode_done")


def test_a_failed_measurement_leaves_the_default_and_retries_next_start(ctx, monkeypatch):
    def boom(models):
        raise RuntimeError("no provider")
    monkeypatch.setattr(ap, "measure", boom)
    t = ap.choose_once(ctx.settings, ctx.models, lambda r: pytest.fail("must not apply"))
    t.join(5)
    assert ctx.settings.get("performance.mode") == "balanced" and not ctx.settings.get("performance.auto_mode_done")
