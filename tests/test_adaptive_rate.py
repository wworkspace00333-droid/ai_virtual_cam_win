"""Adaptive detector rate (under overload the detector slows down, the camera output does not) and the ONNX Runtime
optimised-model cache."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from vcstudio.ai.adaptive import AdaptiveAIRate


def run(a: AdaptiveAIRate, seconds: float, out_fps: float, in_fps: float, t0: float = 0.0, dt: float = 0.5) -> float:
    t = t0
    while t < t0 + seconds:
        a.update(t, out_fps, in_fps)
        t += dt
    return t


def test_overload_steps_the_detector_down_to_a_floor():
    a = AdaptiveAIRate()
    t = run(a, 1.5, 40, 60)                     # overloaded, but not yet for down_after seconds
    assert a.scale == 1.0
    t = run(a, 1.0, 40, 60, t)
    assert a.scale == 0.75 and "slowed" in a.reason
    run(a, 30, 40, 60, t)
    assert a.scale == a.min_scale               # never below the floor
    assert a.apply(20, 5) == max(a.min_fps, 20 * a.min_scale)


def test_headroom_brings_the_rate_back_slowly():
    a = AdaptiveAIRate()
    t = run(a, 10, 40, 60)
    low = a.scale
    t = run(a, 4.5, 60, 60, t)                  # less than up_after: no change yet
    assert a.scale == low
    run(a, 60, 60, 60, t)
    assert a.scale == 1.0 and a.reason == ""


def test_in_between_and_without_video_nothing_changes():
    a = AdaptiveAIRate()
    run(a, 10, 56, 60)                          # 93 %: neither overloaded nor clearly free
    assert a.scale == 1.0
    run(a, 10, 0, 0)                            # no video
    assert a.scale == 1.0


def test_input_rate_is_the_reference_not_the_mode_output():
    a = AdaptiveAIRate()
    run(a, 10, 30, 30)                          # a 30 fps input delivered at 30 fps is not overloaded
    assert a.scale == 1.0


def test_maximum_quality_gets_a_cap_below_what_it_achieves():
    a = AdaptiveAIRate()
    assert a.apply(None, 8.0) is None           # unthrottled while there is headroom
    run(a, 3, 30, 60)
    assert a.apply(None, 8.0) == 8.0 * a.scale


def test_disabled_controller_keeps_the_mode_rate():
    a = AdaptiveAIRate(enabled=False)
    run(a, 30, 10, 60)
    assert a.scale == 1.0 and a.apply(20, 5) == 20


def test_engine_uses_the_scaled_rate_but_keeps_a_fixed_user_rate(tmp_path):
    from vcstudio.ai.detection import DetectionConfig
    from vcstudio.ai.engine import AIEngine
    from vcstudio.ai.model_manager import ModelManager
    eng = AIEngine(None, ModelManager(tmp_path))
    eng.configure(DetectionConfig(), "balanced")         # 20 fps
    eng.load_probe = lambda: (30.0, 60.0)
    t = 0.0
    while eng.adaptive.scale == 1.0 and t < 10:
        eng.adaptive._last = -1e9                        # let the probe run on every call in this test
        eng.adaptive.update(t, 30.0, 60.0)
        t += 0.5
    assert eng.target_fps() == 20 * eng.adaptive.scale < 20
    eng.configure(DetectionConfig(frequency_mode="fixed", fixed_fps=7), "balanced")
    assert eng.target_fps() == 7                         # the profile's explicit rate is not changed
    eng.adaptive._last = -1e9
    eng._update_adaptive()
    assert eng.stats().rate_scale == round(eng.adaptive.scale, 3)


YOLOX_NANO = Path(__file__).resolve().parents[1] / "models" / "yolox_nano.onnx"


@pytest.mark.skipif(not YOLOX_NANO.exists(), reason="run scripts/fetch_models.py")
def test_optimised_model_cache_gives_the_same_results_and_recovers_from_a_bad_entry(tmp_path):
    from vcstudio.ai.onnx_engine import create_session
    x = np.random.default_rng(0).uniform(0, 255, (1, 3, 416, 416)).astype(np.float32)
    plain, _ = create_session(YOLOX_NANO, "cpu_only")
    ref = plain.run(None, {plain.get_inputs()[0].name: x})[0]
    first, _ = create_session(YOLOX_NANO, "cpu_only", tmp_path)           # builds the cache entry
    entries = list(tmp_path.glob("yolox_nano-cpu-*.opt.onnx"))
    assert len(entries) == 1 and not list(tmp_path.glob("*.part"))
    cached, _ = create_session(YOLOX_NANO, "cpu_only", tmp_path)          # loads it
    out = cached.run(None, {cached.get_inputs()[0].name: x})[0]
    assert np.allclose(out, ref, rtol=1e-4, atol=1e-3)
    entries[0].write_bytes(b"not a model")                                 # damaged entry: rebuilt, not fatal
    again, _ = create_session(YOLOX_NANO, "cpu_only", tmp_path)
    assert np.allclose(again.run(None, {again.get_inputs()[0].name: x})[0], ref, rtol=1e-4, atol=1e-3)
    assert entries[0].stat().st_size > 1000
