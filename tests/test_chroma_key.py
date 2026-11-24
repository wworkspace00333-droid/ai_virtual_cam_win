"""0.7.0 chroma key: a synthetic green / blue screen scene with a person-coloured shape, a shadow on the screen,
dark hair and green spill; the background service and the pipeline path."""
from __future__ import annotations

import time

import cv2
import numpy as np

from vcstudio.core.frame import Frame
from vcstudio.replacement import chroma_key as ck
from vcstudio.replacement.background import BackgroundConfig, BackgroundService


def scene(screen=(64, 177, 0), w=320, h=180):
    img = np.zeros((h, w, 4), np.uint8)
    img[..., :3] = screen
    img[..., 3] = 255
    img[:, 230:, :3] = (np.array(screen) * 0.55).astype(np.uint8)          # a shadow on the screen
    cv2.ellipse(img, (160, 110), (50, 70), 0, 0, 360, (120, 150, 200, 255), -1)   # skin-coloured person
    cv2.circle(img, (160, 50), 30, (20, 20, 22, 255), -1)                  # dark hair
    return img


def test_screen_keys_out_person_and_hair_stay_shadows_still_key():
    for color, screen in (("#00b140", (64, 177, 0)), ("#0047bb", (187, 71, 0))):
        a = ck.foreground_alpha(scene(screen)[..., :3], ck.KeySettings(color, feather=0))
        assert a[110, 160] == 1.0 and a[50, 160] == 1.0                    # person, hair
        assert a[20, 20] == 0.0 and a[170, 300] < 0.1                      # screen, screen in shadow
        assert a.mean() < 0.45


def test_similarity_and_smoothness():
    img = scene()[..., :3].copy()
    img[0:10, 0:10] = (90, 170, 40)                                        # a pale, off-key green
    loose = ck.foreground_alpha(img, ck.KeySettings(similarity=0.9, feather=0))
    tight = ck.foreground_alpha(img, ck.KeySettings(similarity=0.05, smoothness=0.05, feather=0))
    assert loose[5, 5] == 0.0 and tight[5, 5] == 1.0
    soft = ck.foreground_alpha(img, ck.KeySettings(similarity=0.05, smoothness=1.0, feather=0))
    assert 0.0 < soft[5, 5] < 1.0                                          # a soft edge in between


def test_despill_removes_the_green_rim_only():
    img = scene()
    img[100:120, 105:112, :3] = (110, 190, 170)                            # green-tinted skin at the edge
    out = ck.despill(img, ck.KeySettings(spill=1.0))
    b, g, r = out[110, 108, :3].astype(int)
    assert g <= max(b, r)                                                  # no green excess left
    assert np.array_equal(out[110, 160], img[110, 160])                    # plain skin unchanged
    assert ck.despill(img, ck.KeySettings(spill=0.0)) is img
    blue = ck.despill(scene((187, 71, 0)), ck.KeySettings("#0047bb", spill=1.0))
    assert (blue[..., 0].astype(int) <= np.maximum(blue[..., 1], blue[..., 2]).astype(int)).all()


def test_keyer_cost_at_1080p():
    big = cv2.resize(scene(), (1920, 1080), interpolation=cv2.INTER_NEAREST)
    k = ck.ChromaKeyer(ck.KeySettings())
    k.probability(big)
    t = time.perf_counter()
    for _ in range(10):
        m = k.probability(big)
    ms = (time.perf_counter() - t) * 100
    assert m.shape == (360, 640)
    print(f"chroma key matte from 1080p: {ms:.1f} ms")
    assert ms < 60


class _Cap:
    def __init__(self, f):
        from vcstudio.core.frame_queue import LatestSlot
        self.slot = LatestSlot()
        self.slot.put(f)


def test_background_service_replaces_the_screen_and_despills(tmp_path):
    img = scene()
    fr = Frame(1, 1.0, 1.0, 320, 180, array=img, source="cam")
    svc = BackgroundService(_Cap(fr), None)
    body = {"background": {"mode": "color", "color": "#ff0000", "source": "chroma", "key_color": "#00b140",
                           "key_spill": 1.0, "edge": 0.5}}
    svc.load_profile(body)
    assert svc.cfg.source == "chroma" and svc.seg.fps == 30.0 and svc.seg.smoothing == 0.0
    keyer = svc.seg._factory()
    svc.seg.masks.put(keyer.probability(img))
    out = svc.apply(svc.despill(fr))
    with out.bgra() as a:
        assert tuple(a[20, 20, :3]) == (0, 0, 255)                         # screen -> the red background
        assert abs(int(a[110, 160, 2]) - 200) < 3                          # the person stays
    cfg = BackgroundConfig.from_profile({"background": {"source": "chroma", "key_similarity": 9, "key_feather": -3}})
    assert cfg.key_similarity == 1.5 and cfg.key_feather == 0
    assert BackgroundConfig.from_profile({"background": {"source": "nope"}}).source == "person"
    svc.load_profile({"background": {"mode": "blur", "source": "person"}})
    assert svc.despill(fr) is fr                                           # only the chroma source despills
