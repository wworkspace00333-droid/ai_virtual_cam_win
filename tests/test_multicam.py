"""0.6.0 Phase 2: multi-camera layouts, age-limited secondary frames, auto switching, and the real pipeline."""
from __future__ import annotations

import time

import numpy as np
import pytest

from vcstudio.core import multicam as mc


def solid(w, h, bgr):
    a = np.zeros((h, w, 4), np.uint8)
    a[..., :3] = bgr
    a[..., 3] = 255
    return a


def cfg(**kw):
    body = {"multicam": {"enabled": True, "sources": [{"id": "cam1", "method": "test_pattern"},
                                                       {"id": "cam2", "method": "test_pattern"}], **kw}}
    return mc.MulticamConfig.from_profile(body)


def test_config_is_validated():
    c = mc.MulticamConfig.from_profile({"multicam": {"enabled": True, "layout": "nonsense", "primary": "ghost",
                                                     "sources": [{"method": "test_pattern"}, {"no": "method"}] +
                                                                [{"method": "screen"}] * 5,
                                                     "pip_size": 9, "missing": "x", "fallback": "main",
                                                     "tiles": [{"source": "ghost"}, {"source": "cam1", "x": 2,
                                                                                      "rotation": 95, "crop": [0, 0, 0.01, 1]}]}})
    assert c.layout == "pip" and c.primary == "main" and c.pip_size == 0.5 and c.missing == "placeholder"
    assert len(c.sources) == mc.MAX_SOURCES and c.fallback == ""
    assert [t.source for t in c.tiles] == ["cam1"] and c.tiles[0].x == 1 and c.tiles[0].rotation == 90
    assert c.tiles[0].crop == (0, 0, 1, 1)                       # a too-small crop falls back to the whole source
    assert mc.MulticamConfig.from_profile({}).active is False
    assert mc.MulticamConfig.from_profile({"multicam": "garbage"}) == mc.MulticamConfig()


@pytest.mark.parametrize("layout, n", [("single", 1), ("pip", 3), ("side_by_side", 2), ("split_v", 2), ("grid", 3)])
def test_layout_tiles(layout, n):
    tiles = mc.layout_tiles(cfg(layout=layout))
    assert len(tiles) == n and tiles[0].source == "main"
    for t in tiles:
        assert 0 <= t.x and t.x + t.w <= 1.0001 and 0 <= t.y and t.y + t.h <= 1.0001


def test_pip_windows_stack_in_the_corner_and_primary_can_be_a_secondary():
    tiles = mc.layout_tiles(cfg(layout="pip", pip_corner="top_left", pip_size=0.25, pip_margin=0.02), primary="cam1")
    assert tiles[0].source == "cam1" and [t.source for t in tiles[1:]] == ["main", "cam2"]
    assert tiles[1].x == 0.02 and tiles[1].y == 0.02 and tiles[2].y > tiles[1].y + tiles[1].h


def test_compose_draws_tiles_where_they_belong():
    main = solid(320, 180, (0, 0, 255))
    tiles = mc.layout_tiles(cfg(layout="side_by_side"))
    out = mc.compose((320, 180), tiles, {"main": main, "cam1": solid(64, 64, (0, 255, 0))})
    assert tuple(out[90, 40, :3]) == (0, 0, 255) and tuple(out[90, 280, :3]) == (0, 255, 0)
    # PiP over the main picture with border and rounded corners; the corner pixel stays the main picture
    pip = mc.layout_tiles(cfg(layout="pip", pip_corner="bottom_right", border_px=2, radius_px=10))
    out = mc.compose((320, 180), pip[1:2], {"cam1": solid(64, 64, (0, 255, 0))}, base=main)
    t = pip[1]
    x0, y0 = int(round(t.x * 320)), int(round(t.y * 180))
    x1, y1 = x0 + int(round(t.w * 320)), y0 + int(round(t.h * 180))
    assert tuple(out[(y0 + y1) // 2, (x0 + x1) // 2, :3]) == (0, 255, 0)
    assert tuple(out[y0, x0, :3]) == (0, 0, 255)                  # rounded corner: main shows through
    assert tuple(out[y0 + 1, (x0 + x1) // 2, :3]) == (255, 255, 255)   # white border
    assert tuple(main[0, 0, :3]) == (0, 0, 255) and out is not main


def test_compose_crop_rotation_opacity_and_placeholder():
    src = solid(100, 50, (255, 0, 0))
    src[:, 50:] = (0, 255, 0, 255)                                # left blue, right green
    t = mc.Tile("cam1", 0, 0, 1, 1, crop=(0.5, 0, 1, 1))
    out = mc.compose((40, 40), [t], {"cam1": src})
    assert tuple(out[20, 20, :3]) == (0, 255, 0)                  # only the right half is used
    rot = mc.compose((40, 80), [mc.Tile("cam1", rotation=90, fit="contain")], {"cam1": src})
    assert rot.shape == (80, 40, 4)
    half = mc.compose((40, 40), [mc.Tile("cam1", opacity=0.5)], {"cam1": solid(10, 10, (200, 200, 200))},
                      base=solid(40, 40, (0, 0, 0)))
    assert 90 <= half[20, 20, 0] <= 110
    ph = mc.compose((200, 100), [mc.Tile("cam9")], {"cam9": None})
    assert ph[50, 5, 0] == 28                                      # dark "no signal" tile


class FakeSup:
    def __init__(self):
        from vcstudio.core.frame_queue import LatestSlot
        self.slot = LatestSlot()

    def stop(self):
        pass


def test_stale_frames_follow_the_missing_policy():
    from vcstudio.core.frame import Frame, host_time_s
    ss = mc.SourceSet(lambda spec: None)
    sup = FakeSup()
    ss._sups["cam1"] = sup
    now = host_time_s()
    sup.slot.put(Frame(1, now, now, 8, 8, array=solid(8, 8, (1, 2, 3)), source="t"))
    c = cfg(missing="hold", max_age_s=0.5, hold_max_s=2.0)
    a = ss.latest("cam1", now, c)
    assert isinstance(a, np.ndarray) and tuple(a[0, 0, :3]) == (1, 2, 3)
    assert ss.latest("cam1", now + 0.1, c) is a                   # same frame: no new copy
    assert isinstance(ss.latest("cam1", now + 1.0, c), np.ndarray)           # stale but held
    assert ss.latest("cam1", now + 5.0, c) is None                            # held too long: placeholder
    assert ss.latest("cam1", now + 1.0, cfg(missing="hide", max_age_s=0.5)) is False
    assert ss.latest("ghost", now, cfg(missing="hide")) is False


def test_auto_switch_has_hysteresis_and_cooldown():
    c = cfg(auto="people", fallback="cam1", hold_s=2.0, cooldown_s=5.0)
    sw = mc.AutoSwitcher()
    assert sw.update(c, 0, 0.0) is None                            # nobody yet: wait for hold_s
    assert sw.update(c, 0, 1.0) is None
    assert sw.update(c, 1, 1.5) is None                            # a person flickers back: timer restarts
    assert sw.update(c, 0, 2.0) is None
    assert sw.update(c, 0, 4.1) == "cam1" and sw.switches == 1
    assert sw.update(c, 2, 5.0) == "cam1"                          # people again, but hold + cooldown
    assert sw.update(c, 2, 7.2) == "cam1"                          # held 2 s, still inside the 5 s cooldown
    assert sw.update(c, 2, 9.2) is None and sw.switches == 2
    assert sw.update(cfg(auto="off"), 0, 100) is None


def test_service_manual_switching_and_passthrough_when_single_main():
    svc = mc.MulticamService(mc.SourceSet(lambda s: None))
    c = cfg(layout="single")
    svc.apply(c, running=False)
    out = solid(64, 36, (9, 9, 9))
    assert svc.process(out, c, people=1) is out                    # single + main: nothing to do
    assert svc.next_camera() == "cam1"
    res = svc.process(out, c, people=1)                            # cam1 is not running: placeholder fills
    assert res is not out and res[18, 2, 0] == 28
    assert svc.next_layout() == "pip" and svc.next_layout() == "side_by_side"
    svc.apply(cfg(layout="grid"), running=False)                   # a new scene clears manual switches
    assert svc.override is None and svc.layout_override is None


def test_pipeline_composes_a_second_live_source(paths):
    from vcstudio.app.application import AppContext
    ctx = AppContext(paths, configure_logging=False)
    try:
        ctx.settings.set("output.camera_enabled", False)
        ctx.settings.set("input.method", "test_pattern")
        ctx.settings.set("input.preferred_width", 640)
        ctx.settings.set("input.preferred_height", 360)
        ctx.settings.set("input.preferred_fps", 30)
        ctx.update_active_profile(lambda b: b.__setitem__("multicam", {
            "enabled": True, "layout": "side_by_side",
            "sources": [{"id": "cam1", "method": "video_file", "video_path": str(_clip()), "video_loop": True}]}))
        ctx.start_capture()
        deadline = time.time() + 20
        got = None
        while time.time() < deadline:
            pair, _ = ctx.pipeline.processed.peek()
            if pair is not None and "+multicam" in pair[1].source and ctx.multicam.stats.stale_tiles == 0:
                got = pair
                break
            time.sleep(0.1)
        assert got is not None, (ctx.multicam.stats, ctx.multicam.sources.stats(), ctx.multicam.sources.errors)
        assert ctx.multicam.sources.ids() == ["cam1"]
        assert ctx.pipeline.stats().output_fps > 5                 # the main input still paces the output
        # a scene without multicam stops the extra source
        ctx.update_active_profile(lambda b: b.__setitem__("multicam", {"enabled": False}))
        assert ctx.multicam.sources.ids() == []
        # this app's own camera, and whatever device the output goes to, are refused as extra sources
        assert "show itself" in ctx.source_loop_risk({"method": "mediafoundation",
                                                       "device_name_hint": "AI Virtual Camera"})
        ctx.settings.set("output.camera_enabled", True)
        ctx.settings.set("output.camera_device", "Some Capture Card")
        assert "feed back" in ctx.source_loop_risk({"method": "mediafoundation",
                                                    "device_name_hint": "Some Capture Card"})
        assert ctx.source_loop_risk({"method": "test_pattern"}) == ""
    finally:
        ctx.stop_capture()
        ctx.shutdown()


def _clip():
    from vcstudio.core.resources import sample
    return sample("clip_h264.mp4")


def test_transition_frames():
    old = np.zeros((4, 10, 4), np.uint8)
    new = np.full((4, 10, 4), 200, np.uint8)
    half = mc.transition_frame(old, new, "fade", 0.5)
    assert abs(int(half[0, 0, 0]) - 100) <= 1
    assert mc.transition_frame(old, new, "fade", 1.0) is new
    s = mc.transition_frame(old, new, "slide", 0.3)
    assert (s[:, :7] == 0).all() and (s[:, 7:] == 200).all()             # the new picture comes in from the right
    assert mc.transition_frame(old, np.zeros((5, 10, 4), np.uint8), "fade", 0.5).shape == (5, 10, 4)


def test_camera_switch_fades_and_cut_does_not():
    tile = np.full((90, 160, 4), 200, np.uint8)

    class Src:
        def latest(self, sid, now, cfg):
            return tile
    main = np.full((180, 320, 4), 20, np.uint8)
    for kind, mid in (("fade", True), ("cut", False)):
        svc = mc.MulticamService(Src())
        cfg = mc.MulticamConfig(enabled=True, layout="single", sources=({"id": "cam1", "enabled": True},),
                                transition=kind, transition_s=1.0)
        svc.process(main, cfg, people=1, now=0.0)                         # the main camera
        svc.override = "cam1"
        first = svc.process(main, cfg, people=1, now=0.1)                  # switched: transition starts
        later = svc.process(main, cfg, people=1, now=0.6)
        done = svc.process(main, cfg, people=1, now=1.2)
        if mid:
            assert int(first[90, 160, 0]) < 60 and 80 < int(later[90, 160, 0]) < 140
            assert svc.stats.transitions == 1
        else:
            assert int(first[90, 160, 0]) == 200 and svc.stats.transitions == 0
        assert int(done[90, 160, 0]) == 200
    cfg = mc.MulticamConfig.from_profile({"multicam": {"transition": "wipe", "transition_s": 99}})
    assert cfg.transition == "cut" and cfg.transition_s == 3.0


def test_each_camera_gets_its_own_colour_preset_before_the_shield():
    from vcstudio.replacement.color import ColorConfig
    tile = np.full((90, 160, 4), 100, np.uint8)
    seen = []

    class Src:
        def latest(self, sid, now, cfg):
            return tile
    svc = mc.MulticamService(Src())
    warm = ColorConfig.from_profile({"color": {"enabled": True, "brightness": 0.3}})
    svc.color_for = lambda name: warm if name == "Warm" else None

    def filt(sid, a, mode):
        seen.append((sid, mode, int(a[0, 0, 0])))
        return a
    svc.tile_filter = filt
    cfg = mc.MulticamConfig.from_profile({"multicam": {"enabled": True, "layout": "grid", "sources": [
        {"id": "cam1", "method": "test_pattern", "color_preset": "Warm", "privacy": "hide"},
        {"id": "cam2", "method": "test_pattern", "privacy": "bogus"}]}})
    svc.process(np.full((180, 320, 4), 20, np.uint8), cfg, people=1, now=1.0)
    d = {sid: (mode, v) for sid, mode, v in seen}
    assert d["cam1"][0] == "hide" and d["cam1"][1] > 100                  # graded, then the shield sees it
    assert d["cam2"] == ("shield", 100)                                    # no preset; unknown mode -> shield
    assert set(svc._graders) == {"cam1"}
