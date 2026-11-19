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
