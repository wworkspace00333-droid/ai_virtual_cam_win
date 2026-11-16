"""Multi-object replacement (roadmap P2.3): each tracked object of one region gets its own asset, in turn, and keeps
it while tracked; the real compositor draws a different picture per person."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from vcstudio.replacement.spec import ReplacementSpec


@dataclass
class R:
    roi_id: str
    track_id: int | None


def comp(tmp_path):
    from vcstudio.replacement.assets import AssetLibrary
    from vcstudio.replacement.compositor import Compositor
    return Compositor(AssetLibrary(tmp_path), "cpu")


def test_spec_keeps_a_clean_pool():
    s = ReplacementSpec.from_dict({"asset_id": "a", "per_object_assets": 1,
                                   "extra_assets": ["b", "a", "b", "", "c"]})
    assert s.per_object_assets is True and s.extra_assets == ["b", "c"] and s.asset_pool() == ["a", "b", "c"]
    assert ReplacementSpec.from_dict({"extra_assets": "nonsense"}).extra_assets == []


def test_objects_get_assets_in_turn_and_keep_them(tmp_path):
    c = comp(tmp_path)
    spec = ReplacementSpec(enabled=True, asset_id="A", per_object_assets=True, extra_assets=["B", "C"])
    assets = {"A": 1, "B": 2, "C": 3}
    order = [c._pick_asset(R("r", t), spec, assets) for t in (7, 3, 9, 12)]
    assert order == ["A", "B", "C", "A"]                          # in order of appearance, wrapping round
    assert [c._pick_asset(R("r", t), spec, assets) for t in (12, 9, 3, 7)] == ["A", "C", "B", "A"]   # kept
    assert c._pick_asset(R("other", 7), spec, assets) == "A"     # each region hands out its own
    assert c._pick_asset(R("r", None), spec, assets) == "A"      # no track: the main asset
    del assets["B"]                                              # a removed asset: pool shrinks, nothing breaks
    assert c._pick_asset(R("r", 3), spec, assets) in ("A", "C")
    c.reset_assignments()
    assert c._pick_asset(R("r", 9), spec, assets) == "A"         # a new scene starts afresh


def test_without_the_option_every_object_gets_the_main_asset(tmp_path):
    c = comp(tmp_path)
    spec = ReplacementSpec(enabled=True, asset_id="A", extra_assets=["B"])
    assert {c._pick_asset(R("r", t), spec, {"A": 1, "B": 2}) for t in range(5)} == {"A"}


def test_long_sessions_forget_old_ids(tmp_path):
    c = comp(tmp_path)
    spec = ReplacementSpec(enabled=True, asset_id="A", per_object_assets=True, extra_assets=["B"])
    for t in range(c.MAX_ASSIGNED + 10):
        c._pick_asset(R("r", t), spec, {"A": 1, "B": 2})
    assert len(c._obj_assign["r"]) <= c.MAX_ASSIGNED


def test_compositor_draws_a_different_picture_per_object(tmp_path):
    """Two tracked people in one auto region: one becomes red, the other green."""
    from vcstudio.replacement.assets import AssetLibrary
    from vcstudio.replacement.compositor import Compositor
    from vcstudio.roi.model import ROI
    from vcstudio.roi.resolver import resolve
    from vcstudio.ai.tracking import TrackedObject
    lib = AssetLibrary(tmp_path / "lib")
    ids = []
    for name, bgr in (("red", (0, 0, 255)), ("green", (0, 255, 0))):
        p = tmp_path / f"{name}.png"
        cv2.imwrite(str(p), np.full((40, 40, 3), bgr, np.uint8))
        ids.append(lib.import_file(p))
    spec = ReplacementSpec(enabled=True, mode="rect", asset_id=ids[0].id, fit="stretch",
                           per_object_assets=True, extra_assets=[ids[1].id])
    roi = ROI(id="people", mode="auto", classes=["person"], padding=0.0)
    tracked = [TrackedObject(1, "person", 0.9, 20, 20, 100, 200, "tracked", 1.0, 0.0),
               TrackedObject(2, "person", 0.9, 200, 20, 280, 200, "tracked", 1.0, 0.0)]
    regions = resolve([roi], tracked, 320, 240)
    frame = np.full((240, 320, 4), 30, np.uint8)
    out = Compositor(lib, "cpu").render(frame, regions, {"people": spec}, {a.id: a for a in ids})
    assert tuple(out[110, 60, :3]) == (0, 0, 255)               # first person: the main asset
    assert tuple(out[110, 240, :3]) == (0, 255, 0)              # second person: the extra
    assert tuple(out[230, 160, :3]) == (30, 30, 30)             # outside both: untouched
