"""First start: measure this PC once and pick the performance mode.

The smallest built-in detector runs a few times on the sample photo with the provider the app would use (CUDA,
DirectML on the best GPU, or CPU). Its median inference time decides the mode:

  median <= PERFORMANCE_MS  -> Performance   (AI target 30/s; leaves room for a larger model)
  median <= BALANCED_MS     -> Balanced      (AI target 20/s)
  otherwise                 -> Low Resource  (AI target 10-15/s, smallest model)

The camera output is 60 fps in every mode (AI never delays it, and the adaptive rate lowers the detector rate under
overload). The choice is made once (settings performance.auto_mode_done) and only replaces the default: a mode the
user already picked is kept. The measurement and the reason are stored in performance.auto_mode_note.
"""
from __future__ import annotations

import logging
import statistics
import threading
import time

log = logging.getLogger(__name__)

PERFORMANCE_MS = 12.0
BALANCED_MS = 35.0
RUNS = 10


def mode_for(median_ms: float) -> str:
    if median_ms <= PERFORMANCE_MS:
        return "performance"
    if median_ms <= BALANCED_MS:
        return "balanced"
    return "low_resource"


def measure(models) -> dict:
    """{'model', 'backend', 'median_ms', 'mode'}: smallest built-in detector on the sample photo."""
    from vcstudio.ai.backend import make_detector
    from vcstudio.ai.testimages import load_bgra
    from vcstudio.core.frame import Frame
    from vcstudio.core.resources import sample_photo
    info = models.resolve("auto", "detect")              # smallest built-in detector
    loaded = models.load(info)
    det = make_detector(loaded)
    img = load_bgra(sample_photo())
    frame = Frame(0, 0, 0, img.shape[1], img.shape[0], array=img, source="auto-profile")
    for _ in range(3):                                   # warm-up: first runs compile / plan
        det.detect(frame)
    times = []
    for _ in range(RUNS):
        t0 = time.perf_counter()
        det.detect(frame)
        times.append((time.perf_counter() - t0) * 1000)
    med = statistics.median(times)
    return {"model": info.name, "backend": loaded.backend, "median_ms": round(med, 1), "mode": mode_for(med)}


def choose_once(settings, models, apply) -> threading.Thread | None:
    """First GUI start: measure in the background, then call apply(result) (the GUI applies the mode on its own
    thread, and only if the mode is still the one from before the measurement). Returns the thread, or None when
    the choice was already made on an earlier start."""
    if settings.get("performance.auto_mode_done"):
        return None
    before = settings.get("performance.mode")

    def work():
        try:
            r = measure(models)
        except Exception as e:                           # no model, no provider: keep the default, try next start
            log.warning("Automatic performance mode not chosen: %s", e)
            return
        r["note"] = (f"{r['model']} on {r['backend']}: {r['median_ms']} ms per frame -> {r['mode']} "
                     f"(<= {PERFORMANCE_MS:g} ms Performance, <= {BALANCED_MS:g} ms Balanced, else Low Resource)")
        r["mode_before"] = before
        log.info("Automatic performance mode: %s", r["note"])
        apply(r)
    from vcstudio.core import maintenance
    return maintenance.start("auto-profile", work)


def apply_result(ctx, r: dict) -> bool:
    """Store the choice (GUI thread). Returns True if the mode was changed (False: the user picked one meanwhile)."""
    changed = ctx.settings.get("performance.mode") == r["mode_before"] and r["mode"] != r["mode_before"]
    if changed:
        ctx.settings.set("performance.mode", r["mode"], persist=False)
        ctx.update_active_profile(lambda b: b.__setitem__("performance_mode", r["mode"]))
    ctx.settings.set("performance.auto_mode_note", r["note"], persist=False)
    ctx.settings.set("performance.auto_mode_done", True)
    return changed
