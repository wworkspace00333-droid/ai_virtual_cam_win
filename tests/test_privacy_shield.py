"""0.6.0 Phase 5: privacy shield — patterns, real text detection (found / missed / false), hold and strict fail-safe,
effects, and the pipeline."""
from __future__ import annotations

import time

import cv2
import numpy as np
import pytest

from vcstudio.ai import text_detect as td
from vcstudio.core.frame import Frame
from vcstudio.replacement import privacy_shield as ps

needs_text = pytest.mark.skipif(not td.available(), reason="text detection model not downloaded")


@pytest.mark.parametrize("text, kinds", [
    ("john.doe@example.com", ["email"]),
    ("call +1 (415) 555-0134", ["phone"]),
    ("4111 1111 1111 1111", ["card"]),                        # Luhn-valid test card number
    ("4111 1111 1111 1112", ["account"]),                     # fails Luhn: not a card, too long for a phone
    ("DE89370400440532013000", ["account"]),                  # IBAN-like
    ("221B Baker Street", ["address"]),
    ("ул Ленина 5", []),                                      # the number must come first: not matched (limit)
    ("cero emisiones", []),
    ("2024", []),                                             # a year is not a phone number
    ("open 9 to 5", []),
])
def test_sensitive_patterns(text, kinds):
    assert ps.sensitive_kinds(text) == kinds


def test_keywords_and_pattern_selection():
    assert ps.sensitive_kinds("Project ORION budget", keywords=("orion",)) == ["keywords"]
    assert ps.sensitive_kinds("john@example.com", patterns=("phone",)) == []
    assert ps.luhn_ok("4111111111111111") and not ps.luhn_ok("4111111111111112") and not ps.luhn_ok("1234")


def test_config_is_validated():
    class S:
        def get(self, k):
            return {"enabled": True, "text": "bogus", "effect": "x", "padding": 99, "fps": 0,
                    "regions": [{"x": 0.9, "y": 0.9, "w": 0.5, "h": 0.5}, {"x": "a"}, {"x": 0, "y": 0, "w": 0, "h": 0}],
                    "patterns": ["email", "nope"], "keywords": [" a ", "", 3]}
    c = ps.ShieldConfig.from_settings(S())
    assert c.text == "all" and c.effect == "pixelate" and c.padding == 2 and c.fps == 0.5
    assert len(c.regions) == 1 and abs(c.regions[0][2] - 0.1) < 1e-9          # clipped to the frame
    assert c.patterns == ("email",) and c.keywords == ("a", "3")


def text_image(lines, w=960, h=540, bg=235):
    img = np.full((h, w, 3), bg, np.uint8)
    for i, (txt, scale) in enumerate(lines):
        cv2.putText(img, txt, (30, 70 + i * 80), cv2.FONT_HERSHEY_SIMPLEX, scale, (20, 20, 20), 2, cv2.LINE_AA)
    return img


@needs_text
def test_real_detector_finds_text_and_not_empty_areas():
    det = td.TextDetector()
    lines = [("john.doe@example.com", 1.2), ("+1 415 555 0134", 1.2), ("4111 1111 1111 1111", 1.0),
             ("ACCOUNT 12345678", 0.9), ("small text here", 0.6)]
    boxes = det.detect(text_image(lines))
    found = {i for i in range(len(lines)) for b in boxes if 70 + i * 80 - 40 <= (b.box[1] + b.box[3]) / 2 <= 70 + i * 80 + 10}
    assert found == set(range(len(lines))), (found, [b.box for b in boxes])  # every line found (recall 5/5 here)
    yy, xx = np.mgrid[0:540, 0:960]
    plain = np.dstack([(xx / 4) % 256, (yy / 3) % 256, np.full_like(xx, 128)]).astype(np.uint8)
    assert det.detect(cv2.GaussianBlur(plain, (0, 0), 3)) == []               # no false boxes on a smooth picture


@needs_text
def test_tiny_text_is_a_documented_miss():
    det = td.TextDetector()
    boxes = det.detect(text_image([("tiny 555 0134", 0.3)]))                   # ~8 px high: below the limit
    assert len(boxes) <= 1                                                     # usually missed: the UI says so


class FakeDet:
    def __init__(self, boxes):
        self.boxes = boxes

    def detect(self, bgr):
        return [td.TextBox(np.array([[x0, y1], [x0, y0], [x1, y0], [x1, y1]], np.int32), (x0, y0, x1, y1), 0.9)
                for x0, y0, x1, y1 in self.boxes]


class FakeRec:
    def __init__(self, texts):
        self.texts = texts

    def read(self, bgr, tb):
        return self.texts[tb.box]


def frame(w=320, h=180):
    a = np.zeros((h, w, 4), np.uint8)
    a[..., 0] = np.arange(w, dtype=np.uint8)[None, :]
    a[..., 3] = 255
    return Frame(1, 1.0, 1.0, w, h, array=a, source="t")


def shield(**cfg):
    s = ps.PrivacyShield(None, None)
    cfg.setdefault("enabled", True)
    s.cfg = ps.ShieldConfig(**cfg)
    return s


def test_hold_then_forget_and_exclusions():
    s = shield(hold_s=1.0, padding=0.0, exclude=((0.5, 0.0, 0.5, 1.0),))
    s.process(frame(), FakeDet([(10, 10, 60, 30), (200, 10, 260, 30)]), now=100.0)
    assert s.boxes_for(320, 180, now=100.5) == [(10, 10, 60, 30)]              # right half excluded
    s.process(frame(), FakeDet([]), now=100.6)
    assert s.boxes_for(320, 180, now=100.7) == [(10, 10, 60, 30)]              # held after a miss
    assert s.boxes_for(320, 180, now=101.2) == []                              # then forgotten


def test_sensitive_mode_hides_only_matches_and_falls_back_to_all_without_a_recogniser():
    boxes = [(10, 10, 60, 30), (10, 60, 60, 80)]
    rec = FakeRec({(10, 10, 60, 30): "4111111111111111", (10, 60, 60, 80): "hello world"})
    s = shield(text="sensitive", padding=0.0)
    s.process(frame(), FakeDet(boxes), rec, now=5.0)
    assert s.boxes_for(320, 180, now=5.1) == [(10, 10, 60, 30)] and s.stats.kinds == {"card": 1}
    s2 = shield(text="sensitive", padding=0.0)
    s2.process(frame(), FakeDet(boxes), None, now=5.0)
    assert len(s2.boxes_for(320, 180, now=5.1)) == 2                           # never shows text it cannot read


def test_strict_mode_holds_and_grows_boxes_when_detection_stops():
    s = shield(strict=True, strict_hold_s=10.0, strict_grow=0.5, padding=0.0, fps=2.0)
    s.process(frame(), FakeDet([(100, 100, 140, 120)]), now=50.0)
    s.stats.state = "error"                                                    # the detector broke
    b = s.boxes_for(320, 180, now=58.0)
    assert b == [(90, 90, 150, 130)] and s.stats.state == "holding"           # kept 8 s later, grown by 0.5 x h
    assert s.boxes_for(320, 180, now=61.0) == []


def test_custom_regions_and_effects_change_only_the_boxes():
    for effect in ps.EFFECTS:
        s = shield(effect=effect, regions=((0.0, 0.0, 0.25, 0.5),), text="off")
        f = frame()
        with f.bgra() as a:
            out = s.apply(a)
            assert out is not a and np.array_equal(out[:, 200:], a[:, 200:])  # outside: untouched
            assert not np.array_equal(out[:90, :80], a[:90, :80])            # inside: hidden
            if effect == "black":
                assert out[:90, :80, :3].max() == 0
    assert shield(enabled=False).apply(np.zeros((4, 4, 4), np.uint8)).shape == (4, 4, 4)


def test_pipeline_hides_text_on_the_output(paths):
    from vcstudio.app.application import AppContext
    ctx = AppContext(paths, configure_logging=False)
    try:
        ctx.settings.set("output.camera_enabled", False)
        ctx.settings.set("input.method", "test_pattern")
        ctx.settings.set("input.preferred_width", 640)
        ctx.settings.set("input.preferred_height", 360)
        ctx.shield.detector_factory = lambda: FakeDet([(40, 40, 200, 100)])
        ctx.settings.set("privacy_shield.effect", "black")
        ctx.settings.set("privacy_shield.padding", 0.0)
        ctx.settings.set("privacy_shield.enabled", True)
        assert ctx.shield.running
        ctx.start_capture()
        deadline, got = time.time() + 15, None
        while time.time() < deadline:
            pair, _ = ctx.pipeline.processed.peek()
            if pair is not None and "+shield" in pair[1].source:
                got = pair
                break
            time.sleep(0.05)
        assert got is not None
        with got[1].bgra() as a:
            assert a[70, 120, :3].max() == 0                                   # hidden on the output
        with got[0].bgra() as a:
            assert a[70, 120, :3].max() > 0                                    # the input itself is untouched
        ctx.pipeline.set_passthrough(True, "test")                            # not strict: passthrough shows input
        ctx.settings.set("privacy_shield.strict", True)                        # strict: shield even in passthrough
        time.sleep(0.5)
        pair, _ = ctx.pipeline.processed.peek()
        assert "+shield" in pair[1].source
    finally:
        ctx.stop_capture()
        ctx.shutdown()


def test_ctc_greedy_and_word_split():
    s = np.zeros((6, 37), np.float32)
    for t, i in enumerate([0, 2, 2, 0, 2, 11]):          # blank, "1", "1" (merged), blank, "1", "a"
        s[t, i] = 1
    assert td.ctc_greedy(s) == "11a"
    gray = np.full((32, 300), 230, np.uint8)
    for x0 in (10, 120, 230):                             # three "words" with wide gaps
        gray[8:24, x0:x0 + 50] = 20
    spans = td.TextRecognizer.words(gray)
    assert len(spans) == 3 and spans[0][0] <= 10 and spans[2][1] >= 280
    long = np.full((32, 600), 230, np.uint8)
    long[8:24, 5:595] = 20                                # one 590 px block: split evenly
    assert len(td.TextRecognizer.words(long)) == 4


@pytest.mark.skipif(not (td.available() and td.recognizer_available()), reason="text models not downloaded")
def test_real_recognition_finds_only_sensitive_lines():
    lines = [("call 415 555 0134", 1.1), ("card 4111 1111 1111 1111", 1.1), ("project orion", 1.1),
             ("hello world", 1.1), ("DE89370400440532013000", 1.1)]
    img = text_image(lines)
    s = shield(text="sensitive", padding=0.0, keywords=("orion",))
    a = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
    s.process(Frame(1, 1.0, 1.0, a.shape[1], a.shape[0], array=a, source="t"), td.TextDetector(),
              td.TextRecognizer(), now=1.0)
    rows = sorted(int((b[1] + b[3]) / 2) for b in s.boxes_for(a.shape[1], a.shape[0], now=1.1))
    expected = [70 + i * 80 - 12 for i in (0, 1, 2, 4)]   # phone, card, keyword, IBAN — not "hello world"
    assert len(rows) == 4 and all(abs(r - e) < 25 for r, e in zip(rows, expected)), (rows, s.stats.kinds)
    assert set(s.stats.kinds) <= {"phone", "card", "keywords", "account"}


def test_faces_hidden_by_outline_not_by_box():
    s = shield(text="off", faces=True, padding=0.0, effect="black")
    oval = np.array([[160, 40], [200, 60], [210, 100], [190, 140], [160, 150], [130, 140], [110, 100], [120, 60]],
                    np.int32)
    s.face_outlines = lambda w, h: [oval]
    a = np.full((180, 320, 4), 200, np.uint8)
    out = s.apply(a)
    assert out[100, 160, :3].max() == 0                   # inside the outline: hidden
    assert out[42, 112, 0] == 200                          # inside the box but outside the oval: visible
    assert np.array_equal(out[:, 250:], a[:, 250:])
    g = ps.grow(oval, 1.5)
    assert g[:, 0].min() < oval[:, 0].min() and g[:, 0].max() > oval[:, 0].max()
    s.cfg = ps.ShieldConfig(enabled=True, text="off", faces=False)
    assert s.apply(a) is a                                 # option off: outlines ignored


def test_app_face_outlines_from_vision(paths):
    from vcstudio.ai.vision import FaceInfo, VisionResult
    from vcstudio.app.application import AppContext
    from vcstudio.core.frame import host_time_s
    ctx = AppContext(paths, configure_logging=False)
    try:
        assert ctx._face_outlines(640, 360) == []          # vision off
        ctx.vision.cfg = type(ctx.vision.cfg)(enabled=True)
        ctx.vision.slot.put(VisionResult(host_time_s(), 320, 180, faces=[FaceInfo((10, 20, 50, 80), np.zeros((5, 2)))]))
        polys = ctx._face_outlines(640, 360)               # no mesh: the box, scaled to the output
        assert len(polys) == 1 and polys[0][:, 0].min() == 20 and polys[0][:, 1].max() == 160
        ctx.vision.slot.put(VisionResult(host_time_s() - 5, 320, 180, faces=[FaceInfo((1, 1, 9, 9), np.zeros((5, 2)))]))
        assert ctx._face_outlines(640, 360) == []          # stale result: nothing
    finally:
        ctx.shutdown()


def _src_frame(seq, w=320, h=180):
    f = frame(w, h)
    f.seq = seq
    return f


def test_extra_cameras_are_checked_in_turn_with_their_own_boxes():
    s = shield()
    frames = {"cam1": _src_frame(1), "cam2": _src_frame(1), "cam3": None}
    s.sources = lambda: frames
    det = FakeDet([(10, 10, 60, 30)])
    assert s.process_next_extra(det, now=10.0) == "cam1"
    assert s.process_next_extra(FakeDet([]), now=10.5) == "cam2"           # cam3 has no picture yet: skipped
    assert s.process_next_extra(det, now=11.0) is None                     # cam1 again, same picture: not re-run
    assert s._extra["cam1"].last_run == 11.0 and s._extra["cam1"].runs == 1
    img = frame().array
    out1 = s.apply_source("cam1", img, now=11.2)
    assert out1 is not img and not np.array_equal(out1[15:25, 20:50], img[15:25, 20:50])
    assert np.array_equal(out1[100:, :], img[100:, :])                    # only its box
    assert s.apply_source("cam2", img, now=11.2) is img                    # nothing found there
    assert s.boxes_for(320, 180, now=11.2) == []                           # the main camera is not affected
    del frames["cam1"]
    s.process_next_extra(det, now=12.0)
    assert "cam1" not in s._extra                                          # a removed source forgets its boxes


def test_strict_hides_an_unchecked_extra_camera_whole():
    s = shield(strict=True)
    s.sources = lambda: {"cam1": _src_frame(1)}
    img = frame().array
    whole = s.apply_source("cam1", img, now=5.0)                           # never checked: hidden whole
    assert s.stats.extra["cam1"]["whole"]
    assert np.abs(whole[..., 0].astype(int) - img[..., 0]).mean() > 1      # every column changed (pixelated)
    s.process_next_extra(FakeDet([]), now=5.0)
    assert s.apply_source("cam1", img, now=5.5) is img                     # checked, nothing found: shown
    assert s.apply_source("cam1", img, now=5.0 + s.extra_fresh_s() + 0.1) is not img   # check too old again
    off = shield(strict=True, extras=False)
    assert off.apply_source("cam1", img, now=5.0) is img                   # switched off: tiles untouched
    loose = shield()
    assert loose.apply_source("cam1", img, now=5.0) is img                 # not strict: unchecked tile shown


def test_multicam_tiles_go_through_the_shield_and_fail_closed():
    from vcstudio.core import multicam as mc
    tile = np.full((90, 160, 4), 200, np.uint8)
    calls = []

    class Src:
        def latest(self, sid, now, cfg):
            return tile

    svc = mc.MulticamService(Src())
    cfg = mc.MulticamConfig(enabled=True, layout="side_by_side", sources=({"id": "cam1", "enabled": True},))

    def filt(sid, a, mode="shield"):
        calls.append(sid)
        b = a.copy()
        b[..., :3] = 0
        return b
    svc.tile_filter = filt
    out = svc.process(np.full((180, 320, 4), 50, np.uint8), cfg, people=1, now=1.0)
    assert calls == ["cam1"] and (out[:, 200:, :3] < 60).all()             # the filtered (black) tile is drawn

    def broken(sid, a, mode="shield"):
        raise RuntimeError("shield down")
    svc.tile_filter = broken
    out = svc.process(np.full((180, 320, 4), 50, np.uint8), cfg, people=1, now=2.0)
    assert not (out[:, 160:, :3] == 200).all(axis=-1).any()               # never the unfiltered picture
    assert svc.stats.stale_tiles == 1


def test_per_camera_privacy_modes():
    s = shield()
    img = frame().array
    hidden = s.apply_source("cam1", img, "hide", now=1.0)
    assert hidden is not img and s.stats.extra["cam1"]["whole"]
    off = ps.PrivacyShield(None, None)                                    # shield switched off: "hide" still hides
    assert off.apply_source("cam1", img, "hide", now=1.0) is not img
    strict = shield(strict=True)
    assert strict.apply_source("cam2", img, "off", now=1.0) is not img     # strict ignores "not checked"
    assert shield().apply_source("cam2", img, "off", now=1.0) is img
