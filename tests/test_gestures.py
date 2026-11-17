"""Gesture control (roadmap P2.7): anchors and NMS of the palm detector, gesture rules on built hand shapes, the
real models on a COCO photo (when installed), the hold / release timing, the service and the Settings card."""
from __future__ import annotations

import io
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from vcstudio.ai import hands
from vcstudio.ai.gestures import GestureConfig, GestureRecognizer, GestureService

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- detector pieces
def test_anchors_match_the_mediapipe_layout():
    a = hands.anchors()
    assert a.shape == (2016, 2)
    assert np.allclose(a[0], [0.5 / 24, 0.5 / 24]) and np.allclose(a[1], a[0])          # 2 per cell, x first
    assert np.allclose(a[2], [1.5 / 24, 0.5 / 24])
    assert np.allclose(a[1152], [0.5 / 12, 0.5 / 12]) and np.allclose(a[1152:1158], a[1152])   # then 6 per cell
    assert np.allclose(a[-1], [11.5 / 12, 11.5 / 12])


def test_nms_keeps_the_best_of_overlapping_boxes():
    boxes = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60], [0, 0, 10, 10.5]], np.float64)
    keep = hands.nms(boxes, np.array([0.6, 0.9, 0.7, 0.5]))
    assert keep == [1, 2]


# ---------------------------------------------------------------- gesture rules
def hand_shape(extended: dict[str, bool], thumb: str = "in") -> np.ndarray:
    """21 MediaPipe landmarks of an upright right hand (image y grows downwards). Fingers straight up or curled
    back towards the palm; thumb tucked in, out to the side or pointing up (then the hand is a fist turned)."""
    lm = np.zeros((21, 2))
    lm[0] = (100, 200)                                                   # wrist
    bases = {"index": (80, 130), "middle": (100, 125), "ring": (120, 130), "pinky": (138, 140)}
    first = {"index": 5, "middle": 9, "ring": 13, "pinky": 17}
    for name, (x, y) in bases.items():
        i = first[name]
        lm[i] = (x, y)
        if extended.get(name):
            lm[i + 1], lm[i + 2], lm[i + 3] = (x, y - 30), (x, y - 50), (x, y - 68)
        else:
            lm[i + 1], lm[i + 2], lm[i + 3] = (x, y - 18), (x, y - 5), (x, y + 8)
    if thumb == "in":
        lm[1:5] = [(85, 185), (78, 168), (82, 152), (90, 145)]
    elif thumb == "out":
        lm[1:5] = [(80, 185), (60, 170), (42, 158), (25, 150)]
    else:                                                                # up
        lm[1:5] = [(78, 180), (66, 150), (62, 118), (60, 88)]
    return lm


@pytest.mark.parametrize("ext, thumb, expect", [
    (dict(index=True, middle=True, ring=True, pinky=True), "out", "open_palm"),
    (dict(index=True, middle=True, ring=True, pinky=True), "in", "open_palm"),
    (dict(), "in", "fist"),
    (dict(index=True, middle=True), "in", "victory"),
    (dict(index=True), "in", "point"),
    (dict(), "up", "thumbs_up"),
    (dict(), "out", ""),                                                  # thumb sideways: not thumbs up
    (dict(index=True, pinky=True), "in", ""),                             # unknown shape
])
def test_gesture_rules(ext, thumb, expect):
    assert hands.classify(hand_shape(ext, thumb)) == expect


@pytest.mark.skipif(not hands.available(), reason="hand models not installed (scripts/fetch_models.py --hands)")
def test_real_models_on_a_photo():
    """COCO 165 (ribbon cutting): two hands. The flat hand over the tie is an open palm; the hand in the scissors
    has curled fingers and a sideways thumb — no gesture."""
    import cv2
    t = hands.HandTracker("cpu")
    img = cv2.imread(str(ROOT / "tests" / "data" / "coco16" / "images" / "000000000165.jpg"))
    found = t.hands(img)
    assert len(found) == 2 and all(h.confidence > 0.9 for h in found)
    assert sorted(h.gesture for h in found) == ["", "open_palm"]
    for h in found:
        x0, y0, x1, y1 = h.box
        assert 0 <= x0 < x1 <= img.shape[1] and 0 <= y0 < y1 <= img.shape[0]
        assert 20 < x1 - x0 < 200                                           # hand-sized, in image pixels
    assert t.hands(np.zeros((480, 640, 3), np.uint8)) == []                 # no hands, no false alarm
    t0 = time.perf_counter()
    for _ in range(5):
        t.hands(img)
    assert (time.perf_counter() - t0) / 5 < 0.2                             # ~30 ms on the 2-core server


# ---------------------------------------------------------------- timing
def test_a_gesture_fires_once_after_being_held():
    r = GestureRecognizer(GestureConfig(hold_s=0.6, release_s=0.3, cooldown_s=1.5))
    fired = [(t, r.update("open_palm", t)) for t in np.arange(0, 3.0, 0.125)]
    hits = [t for t, g in fired if g]
    assert len(hits) == 1 and 0.6 <= hits[0] < 0.75                          # held 0.6 s, then fires once


def test_a_flicker_does_not_fire_and_release_rearms():
    r = GestureRecognizer(GestureConfig(hold_s=0.6, release_s=0.3, cooldown_s=1.0))
    seq = ["open_palm", "open_palm", "", "open_palm", "open_palm", ""] * 3        # never held 0.6 s at 8 fps
    assert not any(r.update(g, i * 0.125) for i, g in enumerate(seq))
    t = 10.0
    assert [r.update("victory", t + k * 0.125) for k in range(6)].count("victory") == 1
    t += 1.0
    for k in range(4):                                                       # hand down for 0.5 s: re-armed
        r.update("", t + k * 0.125)
    t += 0.5
    assert [r.update("victory", t + k * 0.125) for k in range(8)].count("victory") == 1
    # a different gesture while the first is still up does not wait for the release of the first
    t += 1.0
    out = [r.update("fist", t + k * 0.125) for k in range(30)]
    assert out.count("fist") == 1


def test_config_from_settings_ignores_unknown_gestures():
    class S:
        def get(self, k):
            return {"enabled": True, "fps": 99, "hold_s": 0.01,
                    "actions": {"open_palm": "record_toggle", "wave": "x", "fist": None}}
    c = GestureConfig.from_settings(S())
    assert c.enabled and c.fps == 15 and c.hold_s == 0.2
    assert c.actions["open_palm"] == "record_toggle" and c.actions["fist"] == "" and "wave" not in c.actions


# ---------------------------------------------------------------- the service
class FakeFrame:
    def __init__(self):
        self.a = np.zeros((120, 160, 4), np.uint8)

    def bgra(self):
        import contextlib
        return contextlib.nullcontext(self.a)


class FakeSlot:
    def __init__(self):
        self.v = 0

    def peek(self):
        self.v += 1
        return FakeFrame(), self.v


class FakeCapture:
    slot = FakeSlot()


class FakeSettings:
    def __init__(self, **g):
        self.d = {"gestures": {"enabled": True, "fps": 15, "hold_s": 0.2, "actions": g}}

    def get(self, k):
        return self.d.get(k)


def test_service_runs_the_mapped_action():
    shown = {"g": "victory"}

    class Tracker:
        backend = "fake"

        def hands(self, bgr):
            return [hands.Hand(np.zeros((21, 2)), (0, 0, 1, 1), 0.99, True, shown["g"])] if shown["g"] else []
    got, events = [], []
    svc = GestureService(FakeCapture(), FakeSettings(victory="replay_save"), lambda lv, m: events.append(m),
                         tracker_factory=Tracker)
    svc.listeners.append(lambda aid, g: got.append((aid, g)))
    svc.apply_settings()
    try:
        end = time.monotonic() + 5
        while time.monotonic() < end and not got:
            time.sleep(0.02)
        assert got == [("replay_save", "victory")] and svc.stats.state == "running"
        assert svc.stats.backend == "fake" and "victory → replay_save" in svc.stats.last_action
        assert any("gesture victory" in e for e in events)
        time.sleep(0.5)
        assert len(got) == 1                                                 # still held: not again
    finally:
        svc.settings.d["gestures"]["enabled"] = False
        svc.apply_settings()
    assert not svc.running and svc.stats.state == "off"


def test_service_without_models_or_with_broken_models():
    svc = GestureService(FakeCapture(), FakeSettings(), tracker_factory=lambda: None)
    svc.start()
    svc._t.join(3)
    assert svc.stats.state == "no_models"

    def boom():
        raise RuntimeError("bad model")
    svc = GestureService(FakeCapture(), FakeSettings(), tracker_factory=boom)
    svc.start()
    svc._t.join(3)
    assert svc.stats.state == "error" and "bad model" in svc.stats.error
    svc.stop()


# ---------------------------------------------------------------- app and Settings card
def test_gesture_runs_the_action_through_the_window(qtbot, paths):
    """A fired gesture reaches MainWindow.on_action on the GUI thread, like a global hotkey."""
    from vcstudio.app.application import AppContext
    from vcstudio.ui.main_window import MainWindow
    ctx = AppContext(paths, configure_logging=False)
    w = MainWindow(ctx)
    qtbot.addWidget(w)
    try:
        seen = []
        orig = w.on_action
        w.on_action = lambda aid: (seen.append((aid, threading.current_thread() is threading.main_thread())),
                                   orig(aid))
        w.global_hotkey_fired.disconnect()
        w.global_hotkey_fired.connect(w.on_action)
        th = threading.Thread(target=lambda: ctx.gestures._fire("open_palm"))   # default: open palm → privacy
        th.start(); th.join()
        qtbot.waitUntil(lambda: bool(seen), timeout=3000)
        assert seen[0] == ("privacy_toggle", True)
        from vcstudio.replacement import privacy_mode as pm
        assert pm.level_of(ctx.profiles.active()["body"]) != "off"
    finally:
        ctx.shutdown()


def test_settings_card_download_and_switch(qtbot, paths, monkeypatch):
    import hashlib

    from vcstudio.ai import model_download as md
    from vcstudio.ai import open_vocab
    from vcstudio.app.application import AppContext
    from vcstudio.ui.main_window import MainWindow
    monkeypatch.delenv("VCS_NO_NETWORK", raising=False)
    user = paths.root / "models"
    monkeypatch.setattr(open_vocab, "user_models_dir", lambda: user)
    monkeypatch.setattr(hands, "models_dir", lambda: user)
    blobs = {n: f"fake {n}".encode() for n in md.HANDS}
    table = {n: (u, hashlib.sha256(blobs[n]).hexdigest(), lic, 0) for n, (u, _d, lic, _s) in md.HANDS.items()}
    monkeypatch.setattr(md, "HANDS", table)
    by_url = {u: blobs[n] for n, (u, *_r) in table.items()}
    ctx = AppContext(paths, configure_logging=False)
    w = MainWindow(ctx)
    qtbot.addWidget(w)
    try:
        page = next(p for p in w.pages if p.key == "settings")
        page.refresh_gestures()
        assert not page.gest_dl.isHidden() and not page.gest_on.isEnabled()
        calls = []
        page.download_hand_models(opener=lambda url: calls.append(url) or io.BytesIO(by_url[url]))
        qtbot.waitUntil(lambda: not page._gest_dl_thread.is_alive(), timeout=5000)
        page.refresh_gestures()
        assert len(calls) == 2 and all(u.startswith("https://huggingface.co/opencv/") for u in calls)
        assert hands.available() and page.gest_dl.isHidden() and page.gest_on.isEnabled()
        idx = page.gest_combos["thumbs_up"].findData("record_toggle")
        page.gest_combos["thumbs_up"].setCurrentIndex(idx)
        assert ctx.settings.get("gestures.actions")["thumbs_up"] == "record_toggle"
        ctx.gestures._factory = lambda: None                    # the fake files are not real models
        page.gest_on.setChecked(True)
        assert ctx.settings.get("gestures.enabled") is True and ctx.gestures.running or \
            ctx.gestures.stats.state == "no_models"
        page.gest_on.setChecked(False)
        assert not ctx.gestures.running
    finally:
        ctx.shutdown()
