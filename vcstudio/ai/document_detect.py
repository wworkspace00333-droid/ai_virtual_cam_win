"""Cards, documents and licence plates as whole objects (0.6.0 Phase 5, privacy shield "documents").

No bundled model has classes for identity documents, payment cards or plates, so this is geometry plus text: a
convex four-cornered outline whose proportions match a card / document page or a plate, with printed text found
inside it by the text detector. The whole outline is hidden then (the photo on an ID card, the chip and logo of a
payment card, the plate frame), not only its lines of text.

Proportions (long side / short side, measured on the outline, so a tilted object still matches within the margin):
  document   1.20 .. 2.40   ID-1 cards (bank, ID, driving licence: 85.6 x 54 mm = 1.59), passport pages (ID-3: 1.42),
                            A-series paper (1.41), square-format plates (about 2.0)
  plate      3.40 .. 5.80   one-line plates (EU 520 x 110 mm = 4.73, Russia 4.64)

Confirmation by text: a document needs text lines whose centres lie inside it, each at most half of its short side
high (a big sign is not a card), covering at least MIN_TEXT_SHARE of it; a plate needs a text line at least 40 % as
wide as the plate. An outline touching two or more edges of the picture is not an object of its own (a wall, a
vehicle, the frame itself) and is skipped.

Limits (said in the UI): an object whose edge does not stand out from what is behind it (a white card on a white
desk), that is partly covered by fingers at two or more corners, or smaller than MIN_AREA of the picture is not
found as an object; its printing is still hidden as text. A screen or a framed notice with text of the same
proportions is hidden too (false positives err on the side of hiding). Pure image processing: no identification of
the document, its owner or the person holding it.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

KINDS = {"document": (1.20, 2.40), "plate": (3.40, 5.80)}
MIN_AREA = 0.004             # share of the picture (a card about 6 % of the frame width)
MAX_AREA = 0.45
MIN_TEXT_SHARE = 0.06        # text lines must cover this share of a document (a bus front with a logo: 0.055)
WORK_SIDE = 960              # outlines are searched on a copy scaled to this long side


@dataclass
class DocRegion:
    quad: np.ndarray                     # (4, 2) int32 corners in frame pixels
    box: tuple[int, int, int, int]       # x0, y0, x1, y1
    kind: str                            # "document" | "plate"
    texts: int                           # text lines inside


def kind_of(w: float, h: float) -> str:
    """The kind matching the proportions of a w x h outline, or "" (pure: tested)."""
    lo_side, hi_side = min(w, h), max(w, h)
    if lo_side <= 0:
        return ""
    r = hi_side / lo_side
    return next((k for k, (lo, hi) in KINDS.items() if lo <= r <= hi), "")


def _quads(gray: np.ndarray) -> list[np.ndarray]:
    """Convex four-cornered outlines in a grey picture: Canny edges (thresholds from the median) and an Otsu split
    (a light card on a dark desk and the reverse), both closed a little so a rounded corner still closes."""
    H, W = gray.shape
    area = float(H * W)
    g = cv2.GaussianBlur(gray, (5, 5), 0)
    med = float(np.median(g))
    edges = cv2.Canny(g, max(10.0, 0.66 * med), min(255.0, max(40.0, 1.33 * med)))
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8))
    _, otsu = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    out: list[np.ndarray] = []
    for img in (edges, otsu, 255 - otsu):
        cnts, _ = cv2.findContours(img, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts:
            a = cv2.contourArea(c)
            if not MIN_AREA * area <= a <= MAX_AREA * area:
                continue
            hull = cv2.convexHull(c)
            peri = cv2.arcLength(hull, True)
            q = cv2.approxPolyDP(hull, 0.03 * peri, True)
            if len(q) == 4 and cv2.isContourConvex(q):
                quad = q.reshape(4, 2)
            else:                                    # rounded corners: accept a contour that fills its rectangle
                rect = cv2.minAreaRect(c)
                rw, rh = rect[1]
                if rw * rh <= 0 or a / (rw * rh) < 0.85:
                    continue
                quad = cv2.boxPoints(rect)
            if cv2.contourArea(quad.astype(np.float32)) / max(1.0, cv2.contourArea(hull)) > 1.25:
                continue                             # the four corners do not describe this contour
            out.append(quad.astype(np.float32))
    return out


def _sides(quad: np.ndarray) -> tuple[float, float]:
    d = [float(np.linalg.norm(quad[i] - quad[(i + 1) % 4])) for i in range(4)]
    return (d[0] + d[2]) / 2, (d[1] + d[3]) / 2


def _iou(a, b) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0.0


def find(bgr: np.ndarray, text_boxes) -> list[DocRegion]:
    """Cards / documents / plates in `bgr` confirmed by `text_boxes` (anything with .box = x0, y0, x1, y1 in the
    same pixels, e.g. text_detect.TextBox). Largest first, overlapping outlines merged (the larger is kept)."""
    if not len(text_boxes):
        return []
    H, W = bgr.shape[:2]
    s = min(1.0, WORK_SIDE / max(H, W))
    small = cv2.resize(bgr, (max(1, int(W * s)), max(1, int(H * s))), interpolation=cv2.INTER_AREA) if s < 1 else bgr
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    texts = [tuple(float(v) for v in t.box) for t in text_boxes]
    found: list[DocRegion] = []
    for q in sorted(_quads(gray), key=lambda q: -cv2.contourArea(q)):
        quad = q / s
        a, b = _sides(quad)
        kind = kind_of(a, b)
        if not kind:
            continue
        short, long_ = min(a, b), max(a, b)
        x0, y0 = quad.min(0)
        x1, y1 = quad.max(0)
        box = (int(x0), int(y0), int(np.ceil(x1)), int(np.ceil(y1)))
        if any(_iou(box, f.box) > 0.5 for f in found):
            continue
        mx, my = 0.01 * W, 0.01 * H
        if (x0 <= mx) + (y0 <= my) + (x1 >= W - 1 - mx) + (y1 >= H - 1 - my) >= 2:
            continue
        poly = quad.reshape(-1, 1, 2)
        inside = []
        for t in texts:
            cx, cy = (t[0] + t[2]) / 2, (t[1] + t[3]) / 2
            if cv2.pointPolygonTest(poly, (cx, cy), False) >= 0:
                inside.append(t)
        if kind == "document":
            ok = [t for t in inside if min(t[2] - t[0], t[3] - t[1]) <= 0.5 * short]
            if sum((t[2] - t[0]) * (t[3] - t[1]) for t in ok) < MIN_TEXT_SHARE * a * b:
                ok = []
        else:
            ok = [t for t in inside if max(t[2] - t[0], t[3] - t[1]) >= 0.4 * long_]
        if not ok:
            continue
        found.append(DocRegion(np.round(quad).astype(np.int32), box, kind, len(ok)))
    return found
