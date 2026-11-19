"""Resource scheduler (roadmap P2.1): under overload the least important module slows first, the most important
recovers first; idle modules run at full rate; the app wires background segmentation and the detector to it."""
from __future__ import annotations

from vcstudio.core.scheduler import Module, ResourceScheduler


class Clock:
    def __init__(self):
        self.out, self.inp = 60.0, 60.0


def make(clock, active=None):
    s = ResourceScheduler(lambda: (clock.out, clock.inp))
    scales = {}
    active = active or {"background": True, "detector": True}
    for name, prio, lo in (("background", 1, 1 / 3), ("detector", 2, 0.25)):
        s.add(Module(name, prio, lo, active=lambda n=name: active[n],
                     apply=lambda sc, why, n=name: scales.__setitem__(n, sc)))
    return s, scales, active


def run(s, seconds, t0=0.0, dt=0.5):
    t = t0
    while t < t0 + seconds:
        s.step(t)
        t += dt
    return t


def test_overload_slows_background_first_then_detector():
    c = Clock()
    s, scales, _ = make(c)
    c.out = 40                                       # output below 90 % of input
    t = run(s, 2.4)
    assert scales == {"background": 0.75, "detector": 1.0} and s.status.load == "overloaded"
    t = run(s, 10, t)                                # background reaches its floor before the detector is touched
    assert abs(scales["background"] - 1 / 3) < 1e-9 and scales["detector"] < 1.0
    run(s, 60, t)
    assert scales["detector"] == 0.25                # never below the detector's floor
    assert s.status.steps_down >= 6 and "slowed" in s.status.last_action


def test_headroom_restores_the_detector_first_and_slowly():
    c = Clock()
    s, scales, _ = make(c)
    c.out = 40
    t = run(s, 30)
    c.out = 60
    t = run(s, 4.5, t)                               # less than up_after: nothing yet
    assert scales["detector"] == 0.25
    t = run(s, 6, t)
    assert scales["detector"] > 0.25 and abs(scales["background"] - 1 / 3) < 1e-9   # detector first
    run(s, 300, t)
    assert scales == {"background": 1.0, "detector": 1.0} and s.status.load == "ok"


def test_inactive_modules_are_skipped_and_reset():
    c = Clock()
    s, scales, active = make(c, {"background": False, "detector": True})
    c.out = 40
    run(s, 2.4)
    assert scales["detector"] == 0.75 and scales["background"] == 1.0            # background off: detector slows
    active["detector"] = False
    s.step(100)
    assert scales["detector"] == 1.0                 # an idle module is back at full rate for next time


def test_no_video_and_in_between_change_nothing():
    c = Clock()
    s, scales, _ = make(c)
    c.out, c.inp = 0, 0
    run(s, 10)
    assert s.status.load == "idle" and scales == {"background": 1.0, "detector": 1.0}
    c.out, c.inp = 56, 60                            # 93 %: neither overloaded nor clearly free
    run(s, 10, 10)
    assert scales == {"background": 1.0, "detector": 1.0}


def test_a_failing_module_does_not_stop_the_scheduler():
    c = Clock()
    s = ResourceScheduler(lambda: (c.out, c.inp))

    def boom(*a):
        raise RuntimeError("module broke")
    s.add(Module("bad", 1, 0.5, active=lambda: True, apply=lambda sc, why: None, rate_fps=boom, state=boom))
    c.out = 40
    run(s, 3)
    assert s.status.modules[0].rate_fps == 0.0 and s.status.modules[0].scale < 1.0


def test_app_wires_background_and_detector(paths):
    from vcstudio.app.application import AppContext
    ctx = AppContext(paths, configure_logging=False)
    try:
        names = [m.name for m in ctx.scheduler.modules]
        assert names == ["vision", "background", "detector"]             # sorted by priority (0.6.0: vision first)
        assert ctx.ai.load_probe is None                                  # the engine no longer judges load itself
        det = ctx.scheduler.modules[2]
        det.apply(0.5, "test")
        assert ctx.ai.adaptive.scale == 0.5 and ctx.ai.stats().rate_scale == 0.5
        vis = ctx.scheduler.modules[0]
        vis.apply(0.25, "test")
        assert ctx.vision.rate_scale == 0.25
        bg = ctx.scheduler.modules[1]
        bg.apply(0.5, "test")
        assert ctx.background.seg.rate_scale == 0.5
        ctx.scheduler.step()
        st = ctx.scheduler.status
        assert st.load == "idle" and len(st.modules) == 3 and not st.modules[0].active
    finally:
        ctx.shutdown()
    assert ctx.ai.adaptive.scale == 1.0 and ctx.background.seg.rate_scale == 1.0   # reset on stop


def test_performance_page_shows_the_modules(qtbot, paths):
    from vcstudio.app.application import AppContext
    from vcstudio.ui.main_window import MainWindow
    ctx = AppContext(paths, configure_logging=False)
    w = MainWindow(ctx)
    qtbot.addWidget(w)
    try:
        ctx.scheduler.step()
        w.performance.on_scheduler(ctx.scheduler.status)
        t = w.performance.res_table
        assert t.rowCount() == 3 and t.item(0, 0).text() == "AI Vision (face, pose, hands)"
        assert t.item(1, 0).text() == "Background segmentation"
        assert "no video" in w.performance.res_info.text()
    finally:
        ctx.shutdown()
