"""0.6.0 Phase 5: cards, documents and licence plates hidden as whole objects (outline + text inside)."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from vcstudio.ai import document_detect as dd
from vcstudio.ai import text_detect as td
from vcstudio.core.frame import Frame
from vcstudio.replacement import privacy_shield as ps

needs_text = pytest.mark.skipif(not td.available(), reason="text detection model not downloaded")
DATA = Path(__file__).parent / "data"
CARD_AT = (600, 250)


def scene(kind: str, angle: float = 0.0) -> np.ndarray:
    """1280x720 desk-like picture with an ID-1-shaped card (photo + 4 lines), a one-line plate, a frame without
    text, or loose text, rotated by `angle` degrees and placed at CARD_AT."""
    rng = np.random.default_rng(0)
    img = np.full((720, 1280, 3), (70, 90, 110), np.uint8)
    img = cv2.add(img, rng.integers(0, 25, img.shape, dtype=np.uint8))
    cv2.circle(img, (200, 200), 120, (40, 60, 160), -1)
    if kind == "text":
        for i, t in enumerate(["MEETING AT NOON", "ROOM 42"]):
            cv2.putText(img, t, (500, 300 + 60 * i), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (240, 240, 240), 3)
        return img
    if kind == "plate":
        c = np.full((90, 430, 3), (240, 240, 240), np.uint8)
        cv2.rectangle(c, (2, 2), (427, 87), (20, 20, 20), 3)
        cv2.putText(c, "A 123 BC 77", (25, 68), cv2.FONT_HERSHEY_SIMPLEX, 1.9, (20, 20, 20), 5)
    else:
        c = np.full((270, 428, 3), (235, 235, 230), np.uint8)
        if kind == "card":
            cv2.rectangle(c, (20, 60), (120, 190), (150, 120, 100), -1)            # the photo
            for i, t in enumerate(["IDENTITY CARD", "SMITH JOHN", "No 4831 2290 77", "01 02 1990"]):
                cv2.putText(c, t, (140, 60 + i * 45), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (30, 30, 30), 2)
    h, w = c.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    cos, sin = abs(M[0, 0]), abs(M[0, 1])
    nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
    M[0, 2] += nw / 2 - w / 2
    M[1, 2] += nh / 2 - h / 2
    rc = cv2.warpAffine(c, M, (nw, nh), borderValue=(0, 0, 0))
    m = cv2.warpAffine(np.full((h, w), 255, np.uint8), M, (nw, nh))
    x, y = CARD_AT
    np.copyto(img[y:y + nh, x:x + nw], rc, where=(m > 0)[..., None])
    if kind == "frame":
        cv2.putText(img, "EXIT", (100, 600), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (240, 240, 240), 3)
    return img


@pytest.mark.parametrize("w, h, kind", [(85.6, 54, "document"), (54, 85.6, "document"), (125, 88, "document"),
                                        (520, 110, "plate"), (100, 100, ""), (300, 100, ""), (700, 100, ""),
                                        (0, 10, "")])
def test_proportions(w, h, kind):
    assert dd.kind_of(w, h) == kind


@pytest.fixture(scope="module")
def det():
    return td.TextDetector()


@needs_text
@pytest.mark.parametrize("angle", [0, 12, -25])
def test_card_and_plate_found_whole_at_an_angle(det, angle):
    for kind, want in (("card", "document"), ("plate", "plate")):
        img = scene(kind, angle)
        found = dd.find(img, det.detect(img))
        assert [d.kind for d in found] == [want], (kind, angle, found)
        x0, y0, x1, y1 = found[0].box
        assert abs(x0 - CARD_AT[0]) <= 6 and abs(y0 - CARD_AT[1]) <= 6       # the outline, not the text lines
        if kind == "card":
            assert x1 - x0 >= 420 and y1 - y0 >= 260                         # includes the photo at the left


@needs_text
@pytest.mark.parametrize("kind", ["frame", "text"])
def test_outline_without_text_and_text_without_outline_are_not_documents(det, kind):
    img = scene(kind, 12)
    assert dd.find(img, det.detect(img)) == []


@needs_text
def test_no_documents_on_real_photos(det):
    """18 real photos (bus, street, the 16 COCO validation images): a bus front, signs, screens and walls with text
    were found as outlines before the border / size / text-share rules; none may be a document now."""
    files = [DATA / "bus.jpg", DATA / "street.jpg", *sorted((DATA / "coco16" / "images").glob("*.jpg"))]
    assert len(files) >= 18
    hits = {f.name: dd.find(img, det.detect(img)) for f in files if (img := cv2.imread(str(f))) is not None}
    assert not any(hits.values()), {k: [(d.kind, d.box) for d in v] for k, v in hits.items() if v}


def _frame(img):
    a = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
    return Frame(1, 1.0, 1.0, a.shape[1], a.shape[0], array=a, source="t")


class _Rec:
    def read(self, bgr, tb):
        return "hello world"                       # nothing sensitive in the words themselves


@needs_text
def test_shield_hides_the_whole_card_even_when_its_words_are_not_sensitive(det):
    img = scene("card", 12)
    s = ps.PrivacyShield(None, None)
    s.cfg = ps.ShieldConfig(enabled=True, text="sensitive", documents=True, padding=0.0)
    s.process(_frame(img), det, _Rec(), now=1.0)
    assert s.stats.kinds == {"document": 1}
    boxes = s.boxes_for(1280, 720, now=1.1)
    assert len(boxes) == 1
    x0, y0, x1, y1 = boxes[0]
    assert x0 <= CARD_AT[0] and y0 <= CARD_AT[1] and x1 - x0 >= 420
    out = s.apply(cv2.cvtColor(img, cv2.COLOR_BGR2BGRA), now=1.1)
    photo = out[CARD_AT[1] + 140:CARD_AT[1] + 200, CARD_AT[0] + 40:CARD_AT[0] + 100, :3]
    assert not np.array_equal(photo, img[CARD_AT[1] + 140:CARD_AT[1] + 200, CARD_AT[0] + 40:CARD_AT[0] + 100])
    assert np.array_equal(out[600:700, 50:300, :3], img[600:700, 50:300])          # the rest is untouched
    s2 = ps.PrivacyShield(None, None)
    s2.cfg = ps.ShieldConfig(enabled=True, text="off", documents=True)
    s2.process(_frame(scene("plate", 0)), det, now=1.0)
    assert s2.stats.kinds == {"plate": 1}                                         # works with text detection off
    s3 = ps.PrivacyShield(None, None)
    s3.cfg = ps.ShieldConfig(enabled=True, text="off", documents=True)
    s3.process(_frame(scene("text", 0)), det, now=1.0)
    assert s3.boxes_for(1280, 720, now=1.1) == []                                 # loose text is not hidden then


def test_documents_setting_is_read_and_starts_the_thread_without_text():
    class S:
        def get(self, k):
            return {"enabled": True, "text": "off", "documents": True}
    c = ps.ShieldConfig.from_settings(S())
    assert c.documents and c.text == "off"
    sh = ps.PrivacyShield(None, S(), detector_factory=lambda: (_ for _ in ()).throw(RuntimeError("no model")))
    sh.apply_settings()
    try:
        sh._t.join(2)
        assert sh.stats.state == "error"                                          # the thread did start
    finally:
        sh.stop()
