"""0.6.0 soak: every Studio feature on at once for N minutes (headless, synthetic input); memory and frame rate sampled.

    python scripts/pro060_soak.py --minutes 10 [--out reports/pro060_soak.json]

Pass criteria (printed): output within 10 % of the input rate on average, no render errors, memory growth after the
first two minutes under 50 MB per 10 minutes (linear fit), no pipeline restarts.
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=10.0)
    ap.add_argument("--size", default="1280x720@30")
    ap.add_argument("--out", type=Path, default=ROOT / "reports" / "pro060_soak.json")
    a = ap.parse_args()
    import numpy as np
    import psutil

    from vcstudio.app.application import AppContext
    from vcstudio.core.paths import AppPaths
    wh, fps = a.size.split("@")
    w, h = (int(x) for x in wh.split("x"))
    ctx = AppContext(AppPaths(Path(tempfile.mkdtemp(prefix="vcs-soak060-"))), configure_logging=False)
    proc = psutil.Process()
    samples = []
    try:
        s = ctx.settings
        s.set("output.camera_enabled", False)
        s.set("input.method", "test_pattern")
        s.set("input.preferred_width", w)
        s.set("input.preferred_height", h)
        s.set("input.preferred_fps", int(fps))
        s.set("vision", {"enabled": True, "fps": 4, "face": True, "pose": True, "hands": False, "overlay": True})
        s.set("privacy_shield", {**s.get("privacy_shield"), "enabled": True, "text": "all", "fps": 1})
        s.set("automation", {"enabled": True, "rules": [
            {"id": "t", "trigger": {"type": "timer", "every_s": 30}, "action": {"type": "layout", "layout": "pip"},
             "cooldown_s": 0}]})
        ctx.update_active_profile(lambda b: b.update({
            "multicam": {"enabled": True, "layout": "pip", "sources": [{"id": "cam1", "method": "test_pattern"}]},
            "color": {"enabled": True, "exposure": 0.2, "saturation": 1.2, "vignette": 0.3},
            "background": {"mode": "gradient", "quality": "balanced"}}))
        ctx.start_capture()
        t0 = time.monotonic()
        f_prev, t_prev = ctx.pipeline.stats().frames_out, t0
        while time.monotonic() - t0 < a.minutes * 60:
            time.sleep(10)
            st = ctx.pipeline.stats()
            now = time.monotonic()
            samples.append({"t_s": round(now - t0, 1), "rss_mb": round(proc.memory_info().rss / 2**20, 1),
                            "fps": round((st.frames_out - f_prev) / (now - t_prev), 1), "errors": st.render_errors,
                            "restarts": st.restarts, "multicam_ms": round(ctx.multicam.stats.compose_ms, 1),
                            "vision_fps": round(ctx.vision.stats.fps, 1), "shield_runs": ctx.shield.stats.runs,
                            "automation_fired": len(ctx.automation.history)})
            f_prev, t_prev = st.frames_out, now
            print(json.dumps(samples[-1]), flush=True)
    finally:
        ctx.stop_capture()
        ctx.shutdown()
    late = [x for x in samples if x["t_s"] >= 120] or samples
    slope = float(np.polyfit([x["t_s"] for x in late], [x["rss_mb"] for x in late], 1)[0]) if len(late) > 2 else 0.0
    res = {"minutes": a.minutes, "size": a.size, "input": "synthetic test pattern", "samples": samples,
           "avg_fps": round(sum(x["fps"] for x in samples) / max(1, len(samples)), 1),
           "min_fps": min((x["fps"] for x in samples), default=0), "rss_growth_mb_per_10min": round(slope * 600, 1),
           "errors": samples[-1]["errors"] if samples else 0, "restarts": samples[-1]["restarts"] if samples else 0}
    res["pass"] = (res["avg_fps"] >= 0.9 * int(fps) and res["errors"] == 0 and res["restarts"] == 0
                   and res["rss_growth_mb_per_10min"] < 50)
    a.out.write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k != "samples"}))
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
