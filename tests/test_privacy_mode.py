"""Privacy mode (roadmap P2.4): levels add their regions and background, switching off restores the scene."""
from __future__ import annotations

import pytest

from vcstudio.ai.tracking import TrackedObject
from vcstudio.core.profiles import default_profile_body
from vcstudio.replacement import privacy_mode as pm
from vcstudio.roi.model import QUICK_FACE_ID, new_rect, rois_from_profile
from vcstudio.roi.resolver import resolve


def ids(body):
    return [r["id"] for r in body["rois"]]


def test_levels_add_and_remove_their_regions_only():
    b = default_profile_body()
    mine = new_rect("mine", 0.1, 0.1, 0.2, 0.2).to_dict()
    b["rois"] = [mine]
    b["ai"]["face_detection"] = None
    pm.set_level(b, "faces")
    assert ids(b) == [mine["id"], QUICK_FACE_ID] and b["ai"]["face_detection"] == "vision"
    pm.set_level(b, "screens")
    assert ids(b) == [mine["id"], QUICK_FACE_ID, pm.SCREEN_ID]
    pm.set_level(b, "faces")
    assert pm.SCREEN_ID not in ids(b)
    pm.set_level(b, "off")
    assert ids(b) == [mine["id"]] and pm.level_of(b) == "off"          # the user's region is untouched
    with pytest.raises(ValueError):
        pm.set_level(b, "max")


def test_background_is_blurred_at_all_and_restored_after():
    b = default_profile_body()
    b["background"] = {"mode": "image", "asset_id": "beach", "strength": 10}
    pm.set_level(b, "all")
    assert b["background"]["mode"] == "blur" and b["background"]["strength"] >= 25
    pm.set_level(b, "screens")                                          # stepping down restores it
    assert b["background"] == {"mode": "image", "asset_id": "beach", "strength": 10}
    pm.set_level(b, "all")
    pm.set_level(b, "off")
    assert b["background"] == {"mode": "image", "asset_id": "beach", "strength": 10}
    assert "saved_background" not in b["privacy"]


def test_toggle_remembers_the_last_level():
    b = default_profile_body()
    assert pm.toggle(b) == "screens"                                    # default when never used
    pm.set_level(b, "all")
    assert pm.toggle(b) == "off"
    assert pm.toggle(b) == "all"


def test_screens_region_follows_screens_and_faces_follow_faces():
    b = default_profile_body()
    pm.set_level(b, "screens")
    objs = [TrackedObject(1, "laptop", 0.9, 10, 10, 200, 150, "tracked", 1.0, 0.0),
            TrackedObject(2, "face", 0.9, 300, 50, 360, 120, "tracked", 1.0, 0.0),
            TrackedObject(3, "person", 0.9, 280, 20, 400, 400, "tracked", 1.0, 0.0),
            TrackedObject(4, "cell phone", 0.8, 420, 300, 450, 360, "tracked", 1.0, 0.0)]
    regs = resolve(rois_from_profile(b), objs, 640, 480)
    assert sorted(r.label for r in regs) == ["cell phone", "face", "laptop"]      # never the whole person


def test_hotkey_and_preview_selector(qtbot, paths):
    from vcstudio.app.application import AppContext
    from vcstudio.core.config import DEFAULT_HOTKEYS
    from vcstudio.ui.main_window import MainWindow
    assert DEFAULT_HOTKEYS["privacy_toggle"] == "Ctrl+Alt+H"
    ctx = AppContext(paths, configure_logging=False)
    w = MainWindow(ctx)
    qtbot.addWidget(w)
    try:
        w.on_action("privacy_toggle")
        assert pm.level_of(ctx.profiles.active()["body"]) == "screens"
        page = next(p for p in w.pages if p.key == "preview")
        page._sync_buttons()
        assert page.privacy.currentData() == "screens"
        page.privacy.setCurrentIndex(page.privacy.findData("all"))
        assert ctx.profiles.active()["body"]["background"]["mode"] == "blur"
        w.on_action("privacy_toggle")
        assert pm.level_of(ctx.profiles.active()["body"]) == "off"
        assert ctx.profiles.active()["body"].get("background", {}).get("mode", "off") == "off"
    finally:
        ctx.shutdown()
