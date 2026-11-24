"""Scene transitions (0.7.0): cut / fade / slide / dip from the last frame of the old scene into the new one."""
from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from vcstudio.core.scene_transition import SceneTransition, blend, clean
from tests.test_scenes import colour_scene, ctx, wait_for  # noqa: F401  (fixture)


def solid(bgr, w=64, h=36):
    a = np.zeros((h, w, 4), np.uint8)
    a[..., :3] = bgr
    a[..., 3] = 255
    return a


def test_clean():
    assert clean("fade", 1.2) == ("fade", 1.2)
    assert clean("wipe", 1.0) == ("cut", 1.0)
    assert clean("dip", 99) == ("dip", 5.0) and clean("dip", -1) == ("dip", 0.05)
    assert clean("slide", "x")[1] == 0.5


def test_blend_kinds():
    old, new = solid((0, 0, 200)), solid((0, 200, 0))
    mid = blend(old, new, "fade", 0.5)
    assert abs(int(mid[5, 5, 1]) - 100) <= 1 and abs(int(mid[5, 5, 2]) - 100) <= 1
    assert blend(old, new, "cut", 0.2) is new
    assert blend(old, new, "fade", 1.0) is new
    d = blend(old, new, "dip", 0.25)                                 # half way to black, still the old picture
    assert int(d[5, 5, 1]) == 0 and abs(int(d[5, 5, 2]) - 100) <= 1
    d = blend(old, new, "dip", 0.5)
    assert int(d[5, 5, :3].max()) == 0                                # black in the middle
    d = blend(old, new, "dip", 0.75)
    assert int(d[5, 5, 2]) == 0 and abs(int(d[5, 5, 1]) - 100) <= 1
    s = blend(old, new, "slide", 0.25)                               # new comes in from the right
    assert tuple(s[5, 2, :3]) == (0, 0, 200) and tuple(s[5, -2, :3]) == (0, 200, 0)
    assert blend(solid((1, 1, 1), 10, 10), new, "fade", 0.5) is new   # different size: no blend


def test_state_machine():
    tr = SceneTransition()
    old, new = solid((0, 0, 200)), solid((0, 200, 0))
    calls = []

    def prev():
        calls.append(1)
        return old.copy()
    assert tr.process(new, 1, "fade", 1.0, 0.0, prev) is new          # first scene: nothing to come from
    assert not calls and tr.idle_for(1)
    out = tr.process(new, 2, "fade", 1.0, 10.0, prev)                 # a switch: starts from the held old frame
    assert calls == [1] and tr.running and tr.count == 1
    assert int(out[0, 0, 2]) == 200                                   # p = 0: still the old picture
    out = tr.process(new, 2, "fade", 1.0, 10.5, prev)
    assert abs(int(out[0, 0, 1]) - 100) <= 1
    assert tr.process(new, 2, "fade", 1.0, 11.01, prev) is new and not tr.running
    assert tr.idle_for(2) and calls == [1]
    assert tr.process(new, 3, "cut", 1.0, 20.0, prev) is new and calls == [1]   # cut: no copy at all
    assert tr.process(new, 4, "dip", 1.0, 30.0, lambda: None) is new          # nothing held: shown directly
    assert not tr.running


@pytest.mark.timeout(120)
def test_fade_between_scenes_in_the_pipeline(ctx):  # noqa: F811
    colour_scene(ctx, ctx.profiles.active_name, (0, 0, 255))
    red = ctx.profiles.active_name
    colour_scene(ctx, "Green", (0, 255, 0))
    ctx.scenes.set_transition("Green", "fade", 1.5)
    assert ctx.scenes.transition_of("Green") == ("fade", 1.5)
    ctx.start_capture()
    assert wait_for(lambda: ctx.pipeline.stats().frames_out > 10)
    seen: list[tuple] = []
    stop = False

    def collect():
        last = -1
        while not stop:
            pair, ver = ctx.pipeline.processed.peek()
            if pair is not None and ver != last:
                last = ver
                with pair[1].bgra() as a:
                    seen.append(tuple(int(v) for v in a[10, 10, :3]))
            time.sleep(0.002)
    t = threading.Thread(target=collect, daemon=True)
    t.start()
    ctx.scenes.switch("Green")
    assert ctx.pipeline.scene.transition == "fade"
    assert wait_for(lambda: seen and seen[-1] == (0, 255, 0), 20)
    stop = True
    t.join(2)
    mixed = [c for c in seen if 20 < c[1] < 235 and 20 < c[2] < 235]
    assert mixed, seen[:40]                                           # the picture went through red+green mixtures
    assert ctx.pipeline.transition.count == 1
    # editing the active scene republishes its state but is no switch: no new transition
    colour_scene(ctx, "Green", (0, 250, 0))
    assert wait_for(lambda: ctx.pipeline.scene.version and ctx.pipeline.transition.idle_for(ctx.pipeline.scene.entry))
    time.sleep(0.3)
    assert ctx.pipeline.transition.count == 1
    ctx.scenes.switch(red)                                            # red's own transition is the default cut
    time.sleep(0.5)
    assert ctx.pipeline.transition.count == 1


def test_scenes_page_transition(qtbot, ctx):  # noqa: F811
    from vcstudio.ui.main_window import MainWindow
    ctx.profiles.create("Night")
    w = MainWindow(ctx)
    qtbot.addWidget(w)
    page = next(p for p in w.pages if p.key == "scenes")
    page.refresh()
    card = page.cards["Night"]
    assert card.trans.currentData() == "cut" and not card.trans_s.isEnabled()
    card.trans.setCurrentIndex(card.trans.findData("dip"))
    assert ctx.scenes.transition_of("Night") == ("dip", 0.5)
    card = page.cards["Night"]
    card.trans_s.setValue(1.2)
    card.trans_s.editingFinished.emit()
    assert ctx.scenes.transition_of("Night") == ("dip", 1.2)
    assert page.cards["Night"].trans_s.isEnabled()
    with pytest.raises(ValueError):
        ctx.scenes.set_transition("Night", "wipe", 1)
