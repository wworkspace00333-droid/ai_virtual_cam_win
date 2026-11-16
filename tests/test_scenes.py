"""Scenes (roadmap P2.2): slots and hotkeys, per-scene input, and atomic switching — no output frame mixes two scenes."""
from __future__ import annotations

import time

import numpy as np
import pytest

from vcstudio.replacement.spec import ReplacementSpec
from vcstudio.roi.model import new_rect


def wait_for(cond, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def ctx(paths):
    from vcstudio.app.application import AppContext
    c = AppContext(paths, configure_logging=False)
    c.settings.set("input.method", "test_pattern")
    c.settings.set("input.preferred_width", 640)
    c.settings.set("input.preferred_height", 360)
    c.settings.set("output.camera_enabled", False)
    yield c
    c.stop_capture()
    c.shutdown()


def colour_scene(ctx, name: str, bgr: tuple[int, int, int]):
    """A scene that fills the whole frame with one colour (a full-frame region replaced by a solid image)."""
    import cv2
    if name not in ctx.profiles.names():
        ctx.profiles.create(name)
    png = ctx.paths.root / f"solid-{bgr[0]}-{bgr[1]}-{bgr[2]}.png"
    cv2.imwrite(str(png), np.full((90, 160, 3), bgr, np.uint8))
    asset = ctx.assets.import_file(png)
    body = ctx.profiles.get(name)["body"]
    roi = new_rect("fill", 0.0, 0.0, 1.0, 1.0)
    roi.replacement = ReplacementSpec(enabled=True, mode="rect", asset_id=asset.id, fit="stretch").to_dict()
    body = {**body, "rois": [roi.to_dict()], "assets": [asset.to_dict()],
            "ai": {**body.get("ai", {}), "enabled": False}}
    ctx.profiles.update_body(name, body)


def test_slots_explicit_and_automatic(ctx):
    sm = ctx.scenes
    for n in ("B", "C"):
        ctx.profiles.create(n)
    names = ctx.profiles.names()
    assert sm.name_for_slot(1) == names[0] and sm.name_for_slot(2) == names[1]     # list order without slots
    sm.set_slot("C", 1)
    assert sm.name_for_slot(1) == "C" and sm.slot_of("C") == 1
    sm.set_slot("B", 1)                                                              # a slot belongs to one scene
    assert sm.slot_of("B") == 1 and sm.slot_of("C") is None
    with pytest.raises(ValueError):
        sm.set_slot("B", 12)


def test_scene_switch_by_slot_and_its_own_input(ctx, tmp_path):
    sm = ctx.scenes
    ctx.profiles.create("Film")
    sm.set_slot("Film", 3)
    sm.set_input("Film", {"method": "video_file", "video_path": "C:/clips/a.mp4", "video_loop": False, "junk": 1})
    assert sm.input_of("Film") == {"method": "video_file", "video_path": "C:/clips/a.mp4", "video_loop": False}
    assert sm.switch_slot(3) == "Film" and ctx.profiles.active_name == "Film"
    assert ctx.settings.get("input.method") == "video_file"                         # the scene's input came with it
    assert ctx.settings.get("input.video_path") == "C:/clips/a.mp4"
    sm.set_input("Film", None)
    assert sm.input_of("Film") is None


def test_published_state_matches_the_profile(ctx):
    colour_scene(ctx, ctx.profiles.active_name, (0, 0, 255))
    sc = ctx.pipeline.scene
    assert sc is not None and sc.name == ctx.profiles.active_name and sc.replacement_active
    v = sc.version
    colour_scene(ctx, ctx.profiles.active_name, (0, 255, 0))
    assert ctx.pipeline.scene.version > v                                           # every change is a new state


def test_no_output_frame_mixes_two_scenes(ctx):
    """Two scenes that paint the frame red / green; switch back and forth while video flows. Every output frame
    must be one solid colour that belongs to the scene recorded in its meta — never a mixture."""
    colour_scene(ctx, ctx.profiles.active_name, (0, 0, 255))
    red = ctx.profiles.active_name
    colour_scene(ctx, "Green", (0, 255, 0))
    colours: dict[int, tuple] = {}
    seen = []
    ctx.start_capture()
    assert wait_for(lambda: ctx.pipeline.stats().frames_out > 10)
    stop = False

    def collect():
        last = -1
        while not stop:
            pair, ver = ctx.pipeline.processed.peek()
            if pair is not None and ver != last:
                last = ver
                out = pair[1]
                with out.bgra() as a:
                    seen.append((out.meta.get("scene"), tuple(int(v) for v in a[10, 10, :3]),
                                 tuple(int(v) for v in a[-10, -10, :3])))
            time.sleep(0.002)
    import threading
    t = threading.Thread(target=collect, daemon=True)
    t.start()
    for i in range(12):
        ctx.scenes.switch("Green" if i % 2 == 0 else red)
        colours[ctx.pipeline.scene.version] = (0, 255, 0) if i % 2 == 0 else (0, 0, 255)
        time.sleep(0.15)
    stop = True
    t.join(2)
    switched = [s for s in seen if s[0] in colours]
    assert len(switched) > 20
    for version, top_left, bottom_right in switched:
        assert top_left == bottom_right == colours[version], (version, top_left, bottom_right)
    assert len({s[0] for s in switched}) >= 8                                      # most switches reached the output


def test_scenes_page(qtbot, ctx):
    from vcstudio.ui.main_window import MainWindow
    ctx.profiles.create("Night")
    w = MainWindow(ctx)
    qtbot.addWidget(w)
    page = next(p for p in w.pages if p.key == "scenes")
    page.refresh()
    assert set(page.cards) == set(ctx.profiles.names())
    card = page.cards["Night"]
    card.slot.setCurrentIndex(card.slot.findData(5))
    assert ctx.scenes.slot_of("Night") == 5
    card.use_input.click()
    assert ctx.scenes.input_of("Night")["method"] == "test_pattern"
    card.switch_btn.click()
    assert ctx.profiles.active_name == "Night" and not card.switch_btn.isEnabled()
    w.on_action("scene_5")                                                          # hotkey path: already active
    assert ctx.profiles.active_name == "Night"


def test_scene_hotkeys_are_defaults():
    from vcstudio.core.config import DEFAULT_HOTKEYS
    from vcstudio.ui.actions import ACTION_SPECS
    assert DEFAULT_HOTKEYS["scene_1"] == "Ctrl+Alt+1" and DEFAULT_HOTKEYS["scene_9"] == "Ctrl+Alt+9"
    assert {f"scene_{n}" for n in range(1, 10)} <= {a.id for a in ACTION_SPECS}
    assert len(set(DEFAULT_HOTKEYS.values())) == len(DEFAULT_HOTKEYS)              # no duplicate default keys


def test_thumbnail_is_kept_for_the_active_scene(ctx):
    ctx.start_capture()
    assert wait_for(lambda: ctx.pipeline.stats().frames_out > 5)
    ctx.scenes.remember_thumbnail()
    th = ctx.scenes.thumbnail(ctx.profiles.active_name)
    assert isinstance(th, np.ndarray) and th.shape[1] <= 320 and th.shape[2] == 4
