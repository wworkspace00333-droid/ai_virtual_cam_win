"""0.6.0 Phase 4: background layers (colour, gradient, video), mask quality modes, locked tracks."""
from __future__ import annotations

import numpy as np
import pytest

from vcstudio.ai.detection import Detection
from vcstudio.ai.person_segmentation import guided_refine
from vcstudio.ai.tracking import LOCK_BUFFER_S, ByteTracker, TrackerConfig
from vcstudio.core.frame import Frame
from vcstudio.replacement import background as bgm


def test_config_and_fills():
    c = bgm.BackgroundConfig.from_profile({"background": {"mode": "gradient", "color": "#ff0000",
                                                          "color2": "#0000ff", "direction": "bogus",
                                                          "quality": "ultra"}})
    assert c.mode == "gradient" and c.direction == "vertical" and c.quality == "balanced"
    g = bgm.make_fill("gradient", "#ff0000", "#0000ff", "vertical", 16, 9)
    assert tuple(g[0, 0, :3]) == (0, 0, 255) and tuple(g[-1, 0, :3]) == (255, 0, 0)      # red top, blue bottom
    h = bgm.make_fill("gradient", "#000000", "#ffffff", "horizontal", 11, 4)
    assert h[0, 0, 0] == 0 and h[0, -1, 0] == 255 and h[0, 5, 0] == 127
    c1 = bgm.make_fill("color", "#102030", "", "vertical", 4, 4)
    assert tuple(c1[2, 2]) == (0x30, 0x20, 0x10, 255)
    assert bgm.BackgroundConfig.from_profile({"background": {"mode": "nope"}}).mode == "off"


class FakeSeg:
    backend = "fake"

    def probability(self, img):
        m = np.zeros((192, 192), np.float32)
        m[:, 64:128] = 1.0                       # a person in the middle third
        return m


class Cap:
    def __init__(self, frame):
        from vcstudio.core.frame_queue import LatestSlot
        self.slot = LatestSlot()
        self.slot.put(frame)


def _frame(w=192, h=108):
    a = np.full((h, w, 4), 90, np.uint8)
    a[..., 3] = 255
    return Frame(1, 1.0, 1.0, w, h, array=a, source="t")


@pytest.mark.parametrize("mode, extra, expect", [
    ("color", {"color": "#00ff00"}, (0, 255, 0)),
    ("gradient", {"color": "#00ff00", "color2": "#00ff00"}, (0, 255, 0)),
])
def test_colour_backgrounds_replace_only_the_background(mode, extra, expect):
    f = _frame()
    svc = bgm.BackgroundService(Cap(f), assets=None, segmenter_factory=FakeSeg)
    svc.load_profile({"background": {"mode": mode, **extra}})
    assert svc.active
    svc.seg.masks.put(FakeSeg().probability(None))
    out = svc.apply(f)
    with out.bgra() as a:
        assert tuple(a[54, 5, :3]) == expect                  # background replaced
        assert tuple(a[54, 96, :3]) == (90, 90, 90)           # person kept
    ops = svc.ops(f)                                          # the GPU layer path accepts the same picture
    assert ops and ops[0].src.shape[2] == 4


def test_quality_sets_smoothing_and_refines_edges():
    f = _frame()
    svc = bgm.BackgroundService(Cap(f), assets=None, segmenter_factory=FakeSeg)
    for q, sm in bgm.QUALITY.items():
        svc.load_profile({"background": {"mode": "color", "quality": q}})
        assert svc.seg.smoothing == sm
    svc.seg.masks.put(FakeSeg().probability(None))
    out = svc.apply(f)                                        # quality mode runs the guided filter without errors
    assert out is not f


def test_guided_filter_moves_the_mask_edge_onto_the_picture_edge():
    import cv2
    guide = np.zeros((240, 320, 3), np.uint8)
    guide[:, 160:] = 255                                      # the picture's edge at x = 160
    mask = np.zeros((240, 320), np.float32)
    mask[:, 152:] = 1.0                                       # the model's edge 8 px off, and soft
    blurry = cv2.GaussianBlur(mask, (0, 0), 6)
    ref = cv2.resize(guided_refine(blurry, guide, radius=8, eps=1e-4), (320, 240))
    gb, gr = np.abs(np.diff(blurry[120])), np.abs(np.diff(ref[120]))
    assert gb.argmax() == 151 and gr.argmax() == 159          # steepest step now on the picture's edge
    assert gr.max() > 2 * gb.max()


def test_video_background_through_the_app(paths, tmp_path):
    import time
    from vcstudio.app.application import AppContext
    from vcstudio.core.resources import sample
    ctx = AppContext(paths, configure_logging=False)
    try:
        asset = ctx.assets.import_file(sample("clip_h264.mp4"))
        ctx.update_active_profile(lambda b: (b["assets"].append(asset.to_dict()),
                                             b.__setitem__("background", {"mode": "video", "asset_id": asset.id})))
        assert ctx.background.active and ctx.pipeline.scene.bg_cfg.mode == "video"
        deadline = time.time() + 10
        pic = None
        while time.time() < deadline and pic is None:
            pic = ctx._background_video_frame(asset.id, time.monotonic())
            time.sleep(0.05)
        assert pic is not None and pic.ndim == 3 and pic.shape[2] == 4
        assert ctx._background_video_frame("missing", 0.0) is None
    finally:
        ctx.shutdown()


def det(x, y, w=40, h=80, label="person", conf=0.9):
    return Detection(label, conf, x, y, x + w, y + h)


def test_locked_track_survives_a_long_occlusion_and_is_reacquired_by_position():
    cfg = TrackerConfig(track_buffer_s=1.0, min_hits=1)
    tr = ByteTracker(cfg)
    t = 0.0
    for i in range(5):
        tr.update([det(100 + i * 2, 100)], t, (640, 360))
        t += 0.1
    tid = tr.snapshot()[0].track_id
    assert tr.lock(tid) and tr.snapshot()[0].locked
    assert not tr.lock(999)
    for _ in range(30):                                       # 3 s without detections: longer than track_buffer_s
        tr.update([], t, (640, 360))
        t += 0.1
    assert any(x.track_id == tid for x in tr.tracks())        # kept only because it is locked
    tr.update([det(150, 105)], t, (640, 360))                 # back 40 px further: IoU with the old box is low
    snap = tr.snapshot(t)
    assert [o.track_id for o in snap] == [tid] and snap[0].locked
    assert len(tr.history(tid)) == 6
    tr.update([], t + 0.1, (640, 360))                        # lost from here
    tr.update([], t + 0.2 + LOCK_BUFFER_S, (640, 360))
    assert tr.tracks() == [] and tid not in tr.locked         # even a locked object expires eventually


def test_unlocked_track_expires_and_a_new_id_is_given():
    tr = ByteTracker(TrackerConfig(track_buffer_s=1.0, min_hits=1))
    t = 0.0
    for _ in range(3):
        tr.update([det(100, 100)], t, (640, 360))
        t += 0.1
    tid = tr.snapshot()[0].track_id
    for _ in range(30):
        tr.update([], t, (640, 360))
        t += 0.1
    tr.update([det(150, 105)], t, (640, 360))
    tr.update([det(150, 105)], t + 0.1, (640, 360))
    assert all(o.track_id != tid for o in tr.snapshot(t + 0.1))


def test_framing_can_follow_only_locked_people():
    from vcstudio.ai.tracking import TrackedObject
    from vcstudio.replacement.framing import AutoFramer, FramingConfig

    def person(tid, x0, locked=False):
        return TrackedObject(tid, "person", 0.9, x0, 100, x0 + 80, 300, "tracked", 1.0, 0.0, locked=locked)
    people = [person(1, 40), person(2, 520, locked=True)]
    f = AutoFramer(FramingConfig(enabled=True, locked_only=True))
    x, y, w, h = f.target(people, 640, 360)
    assert x <= 520 and x + w >= 600                          # the locked person on the right is inside
    f2 = AutoFramer(FramingConfig(enabled=True, locked_only=False))
    x2, _, w2, _ = f2.target(people, 640, 360)
    assert x2 <= 40 and w2 > w and x > x2 + 50                # both people; the locked-only crop is smaller
    unlocked = [person(1, 40), person(2, 520)]
    assert f.target(unlocked, 640, 360) == f2.target(unlocked, 640, 360)   # nobody locked: everyone
    assert FramingConfig.from_profile({"framing": {"locked_only": True}}).locked_only
