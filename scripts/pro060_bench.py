"""0.6.0 performance check: output FPS, latency p50/p95, per-frame processing, CPU and memory of the real pipeline
(headless AppContext, synthetic test-pattern input, camera output off) for the baseline and each Studio feature.

    python scripts/pro060_bench.py [--seconds 10] [--out reports/pro060_bench.json]

Synthetic input: the numbers say what the processing costs on this PC, not how a webcam behaves.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["VCS_NO_AUTOSTART"] = "1"


def scenario_profiles() -> dict:
    return {
        "baseline": {},
        "multicam_pip": {"multicam": {"enabled": True, "layout": "pip",
                                      "sources": [{"id": "cam1", "method": "test_pattern"}]}},
        "color_tone_sat": {"color": {"enabled": True, "exposure": 0.3, "contrast": 1.15, "saturation": 1.2}},
        "color_all": {"color": {"enabled": True, "exposure": 0.3, "contrast": 1.15, "saturation": 1.2,
                                "sharpness": 0.4, "vignette": 0.3, "grain": 0.2, "glow": 0.2}},
        "background_gradient": {"background": {"mode": "gradient", "quality": "balanced"}},
    }


def run(seconds: float, w: int, h: int, fps: int, name: str, body: dict, settings: dict) -> dict:
    import psutil

    from vcstudio.app.application import AppContext
    from vcstudio.core.paths import AppPaths
    tmp = Path(tempfile.mkdtemp(prefix="vcs-bench060-"))
    ctx = AppContext(AppPaths(tmp), configure_logging=False)
    proc = psutil.Process()
    try:
        ctx.settings.set("output.camera_enabled", False)
        ctx.settings.set("input.method", "test_pattern")
        ctx.settings.set("input.preferred_width", w)
        ctx.settings.set("input.preferred_height", h)
        ctx.settings.set("input.preferred_fps", fps)
        for k, v in settings.items():
            ctx.settings.set(k, v)
        ctx.update_active_profile(lambda b: b.update(body) or b["ai"].__setitem__("enabled", False))
        ctx.start_capture()
        time.sleep(3.0)                                    # warm-up: models, first frames
        proc.cpu_percent(None)
        f0 = ctx.pipeline.stats().frames_out
        t0 = time.monotonic()
        rss = []
        while time.monotonic() - t0 < seconds:
            time.sleep(0.5)
            rss.append(proc.memory_info().rss / 2**20)
        st = ctx.pipeline.stats()
        el = time.monotonic() - t0
        return {"scenario": name, "size": f"{w}x{h}@{fps}", "output_fps": round((st.frames_out - f0) / el, 1),
                "latency_p50_ms": round(st.latency_ms_p50, 1), "latency_p95_ms": round(st.latency_ms_p95, 1),
                "process_ms": round(st.process_ms, 1), "cpu_percent_of_one_core": round(proc.cpu_percent(None), 0),
                "rss_mb": round(max(rss), 0), "inputs_skipped": st.inputs_skipped, "errors": st.render_errors,
                "backend": st.backend}
    finally:
        ctx.stop_capture()
        ctx.shutdown()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--out", type=Path, default=ROOT / "reports" / "pro060_bench.json")
    ap.add_argument("--sizes", default="1280x720@30,1920x1080@30,1920x1080@60")
    a = ap.parse_args()
    rows = []
    extra = {"vision_face": ({}, {"vision": {"enabled": True, "fps": 6, "face": True, "pose": False, "hands": False,
                                             "overlay": True}}),
             "shield_text": ({}, {"privacy_shield": {"enabled": True, "text": "all", "fps": 2}})}
    for size in a.sizes.split(","):
        wh, fps = size.split("@")
        w, h = (int(x) for x in wh.split("x"))
        for name, body in scenario_profiles().items():
            rows.append(run(a.seconds, w, h, int(fps), name, body, {}))
            print(json.dumps(rows[-1]), flush=True)
        for name, (body, settings) in extra.items():
            rows.append(run(a.seconds, w, h, int(fps), name, body, settings))
            print(json.dumps(rows[-1]), flush=True)
    import platform
    a.out.write_text(json.dumps({"machine": platform.processor(), "cpu_count": os.cpu_count(),
                                 "input": "synthetic test pattern", "rows": rows}, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
