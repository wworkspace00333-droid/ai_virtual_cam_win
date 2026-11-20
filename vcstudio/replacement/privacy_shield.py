"""AI Privacy Shield (0.6.0 Phase 5): hide text and chosen areas on the camera output. Extends privacy mode
(vcstudio/replacement/privacy_mode.py: faces, screens, phones, books by YOLO class) — it does not replace it.

Settings section "privacy_shield" (global: a scene switch must not switch privacy off by accident):
  enabled      on / off
  text         "off" | "all" (every text region found is hidden) | "sensitive" (only text that matches a pattern:
               needs the recognition model; without it, "sensitive" falls back to "all" — never to showing text)
  patterns     which kinds count as sensitive: email, phone, card (13-19 digits, Luhn-checked), account (8+ digits or
               an IBAN-like code), address (number + street word), keywords (the user's own list)
  effect       "pixelate" | "blur" | "black"
  padding      extra margin around each box, share of its height
  sensitivity  0..1, text detector threshold (more = also faint text, more false boxes)
  hold_s       a box stays hidden this long after the detector last saw it (detection runs ~2×/s)
  strict       fail-safe: while the detector is late, failing or restarting, every box seen in the last
               `strict_hold_s` stays hidden, grown by `strict_grow` (the camera may have moved), and the shield is
               drawn even in emergency passthrough; the status says "holding" so the user knows
  regions      the user's own rectangles (x, y, w, h in 0..1) always hidden
  exclude      rectangles never hidden by text detection (e.g. the user's own name badge)

Runs on the INPUT frame on its own thread (newest frame only, `fps` default 2); the output thread only draws the
current boxes (a pixelation of a few boxes: about a millisecond). Limits, said plainly in the UI: text smaller than
about 12 px, strongly slanted, handwritten or very low-contrast is often missed; the English recogniser reads
letters and digits only, so e-mail addresses are recognised only as "all text"; identity documents, payment cards
and licence plates are found as text regions (their printing), not as objects (the bundled detectors have no such
classes). It reduces what leaves the PC; it is not a guarantee.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

log = logging.getLogger(__name__)

TEXT_MODES = ("off", "all", "sensitive")
EFFECTS = ("pixelate", "blur", "black")
PATTERNS = ("email", "phone", "card", "account", "address", "keywords")
MAX_READ = 12                # text boxes read per pass (30-160 ms each on the CPU); the rest stay hidden, unread

_EMAIL = re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", re.I)
_PHONE = re.compile(r"\+?\(?\d[\d\s().-]{5,}\d")       # a whole run of digits and separators
_DIGITS = re.compile(r"\d[\d\s-]{11,30}\d")
_IBAN = re.compile(r"\b[a-z]{2}\d{2}[a-z0-9]{10,30}\b", re.I)
_LONGNUM = re.compile(r"\d{8,}")
_ADDRESS = re.compile(r"\b\d{1,5}[a-z]?\s+(?:[a-zа-яäöüß]+\s+){0,3}(street|st|avenue|ave|road|rd|blvd|lane|ln|"
                      r"drive|dr|ул|улица|пр|проспект|strasse|straße|rue|calle)\b", re.I)


def luhn_ok(digits: str) -> bool:
    d = [int(c) for c in digits if c.isdigit()]
    if not 13 <= len(d) <= 19:
        return False
    total = 0
    for i, v in enumerate(reversed(d)):
        if i % 2:
            v *= 2
            if v > 9:
                v -= 9
        total += v
    return total % 10 == 0


def sensitive_kinds(text: str, patterns: tuple[str, ...] = PATTERNS, keywords: tuple[str, ...] = ()) -> list[str]:
    """Which sensitive kinds `text` contains (pure: tested directly). Recogniser output has no punctuation, so the
    number patterns also accept runs of digits with spaces."""
    t = text.strip()
    found = []
    if "email" in patterns and _EMAIL.search(t):
        found.append("email")
    if "card" in patterns and any(luhn_ok(m.group()) for m in _DIGITS.finditer(t)):
        found.append("card")
    if "phone" in patterns and "card" not in found:
        for m in _PHONE.finditer(t):
            n = sum(c.isdigit() for c in m.group())
            if 7 <= n <= 15:
                found.append("phone")
                break
    if "account" in patterns and (_IBAN.search(t.replace(" ", "")) or
                                  (_LONGNUM.search(t.replace(" ", "")) and "card" not in found and
                                   "phone" not in found)):
        found.append("account")
    if "address" in patterns and _ADDRESS.search(t):
        found.append("address")
    if "keywords" in patterns:
        low = t.casefold()
        if any(k and k.casefold() in low for k in keywords):
            found.append("keywords")
    return found


def _rect(d) -> tuple[float, float, float, float] | None:
    try:
        x, y, w, h = (float(d[k]) for k in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return None
    x, y = min(1.0, max(0.0, x)), min(1.0, max(0.0, y))
    w, h = min(1.0 - x, max(0.0, w)), min(1.0 - y, max(0.0, h))
    return (x, y, w, h) if w > 0.002 and h > 0.002 else None


@dataclass(frozen=True)
class ShieldConfig:
    enabled: bool = False
    text: str = "all"
    patterns: tuple[str, ...] = PATTERNS
    keywords: tuple[str, ...] = ()
    effect: str = "pixelate"
    padding: float = 0.25
    sensitivity: float = 0.5
    fps: float = 2.0
    hold_s: float = 1.5
    strict: bool = False
    strict_hold_s: float = 10.0
    strict_grow: float = 0.5
    regions: tuple[tuple[float, float, float, float], ...] = ()
    exclude: tuple[tuple[float, float, float, float], ...] = ()

    @staticmethod
    def from_settings(settings) -> ShieldConfig:
        try:
            d = settings.get("privacy_shield") or {}
        except KeyError:
            d = {}

        def num(key, lo, hi, default):
            try:
                return min(hi, max(lo, float(d.get(key, default))))
            except (TypeError, ValueError):
                return default
        pats = tuple(p for p in (d.get("patterns") or PATTERNS) if p in PATTERNS)
        kws = tuple(str(k).strip() for k in (d.get("keywords") or []) if str(k).strip())[:50]
        regs = tuple(r for r in (_rect(x) for x in (d.get("regions") or []) if isinstance(x, dict)) if r)[:20]
        excl = tuple(r for r in (_rect(x) for x in (d.get("exclude") or []) if isinstance(x, dict)) if r)[:20]
        return ShieldConfig(bool(d.get("enabled", False)), d.get("text") if d.get("text") in TEXT_MODES else "all",
                            pats, kws, d.get("effect") if d.get("effect") in EFFECTS else "pixelate",
                            num("padding", 0, 2, 0.25), num("sensitivity", 0, 1, 0.5), num("fps", 0.5, 10, 2.0),
                            num("hold_s", 0, 30, 1.5), bool(d.get("strict", False)), num("strict_hold_s", 1, 120, 10),
                            num("strict_grow", 0, 3, 0.5), regs, excl)


@dataclass
class Hidden:
    box: tuple[float, float, float, float]       # x0, y0, x1, y1 in frame pixels (before padding)
    last_seen: float                             # monotonic
    kind: str = "text"


@dataclass
class ShieldStats:
    state: str = "stopped"                       # stopped | running | no_models | error | holding (strict)
    runs: int = 0
    ms: float = 0.0
    boxes: int = 0
    hidden: int = 0
    sensitive: int = 0
    recogniser: bool = False
    error: str = ""
    last_run: float = 0.0
    kinds: dict = field(default_factory=dict)


class PrivacyShield:
    def __init__(self, capture, settings, log_event=None, detector_factory=None, recognizer_factory=None):
        self.capture, self.settings = capture, settings
        self._log = log_event or (lambda lv, m: None)
        self.detector_factory = detector_factory
        self.recognizer_factory = recognizer_factory
        self.cfg = ShieldConfig()
        self.stats = ShieldStats()
        self._hidden: list[Hidden] = []
        self._frame_size = (0, 0)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._t: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._t is not None and self._t.is_alive()

    def apply_settings(self) -> None:
        cfg = ShieldConfig.from_settings(self.settings)
        need_thread = cfg.enabled and cfg.text != "off"
        restart = self.running and (cfg.text != self.cfg.text or cfg.sensitivity != self.cfg.sensitivity)
        self.cfg = cfg
        if restart or (self.running and not need_thread):
            self.stop()
        if need_thread and not self.running:
            self.start()

    def start(self) -> None:
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, args=(self._stop,), name="privacy-shield", daemon=True)
        self._t.start()

    def stop(self) -> None:
        self._stop.set()
        if self._t is not None:
            self._t.join(3)
        self._t = None
        if not self.cfg.strict:
            with self._lock:
                self._hidden = []
        self.stats.state = "stopped"

    # ------------------------------------------------------------------ detection thread
    def _run(self, stop: threading.Event) -> None:
        from vcstudio.ai import text_detect as td
        try:
            if self.detector_factory is not None:
                det = self.detector_factory()
            elif td.available():
                det = td.TextDetector()
            else:
                self.stats.state = "no_models"
                return
            if hasattr(det, "set_sensitivity"):
                det.set_sensitivity(self.cfg.sensitivity)
            rec = None
            if self.cfg.text == "sensitive":
                if self.recognizer_factory is not None:
                    rec = self.recognizer_factory()
                elif td.recognizer_available():
                    rec = td.TextRecognizer()
            self.stats.recogniser = rec is not None
        except Exception as e:
            self.stats.state, self.stats.error = "error", f"{type(e).__name__}: {e}"
            self._log("ERROR", f"privacy shield: text model did not load: {self.stats.error}")
            return
        self.stats.state, self.stats.error = "running", ""
        last_ver = None
        while not stop.is_set():
            t0 = time.monotonic()
            frame, ver = self.capture.slot.peek()
            if frame is not None and ver != last_ver:
                last_ver = ver
                try:
                    self.process(frame, det, rec)
                except Exception as e:
                    self.stats.error = f"{type(e).__name__}: {e}"
                    log.exception("privacy shield frame failed")
            stop.wait(max(0.0, 1.0 / self.cfg.fps - (time.monotonic() - t0)))

    def process(self, frame, det, rec=None, now: float | None = None) -> None:
        """One detection pass: refresh the hidden boxes (also called directly by tests)."""
        cfg = self.cfg
        now = time.monotonic() if now is None else now
        with frame.bgra() as a:
            bgr = np.ascontiguousarray(a[..., :3])
        t = time.perf_counter()
        boxes = det.detect(bgr)
        W, H = frame.width, frame.height
        keep = []
        read = 0
        kinds: dict[str, int] = {}
        for b in boxes:
            x0, y0, x1, y1 = b.box
            cx, cy = (x0 + x1) / 2 / W, (y0 + y1) / 2 / H
            if any(ex <= cx <= ex + ew and ey <= cy <= ey + eh for ex, ey, ew, eh in cfg.exclude):
                continue
            kind = "text"
            if cfg.text == "sensitive" and rec is not None and read < MAX_READ:
                read += 1
                txt = rec.read(bgr, b)
                found = sensitive_kinds(txt, cfg.patterns, cfg.keywords)
                if not found:
                    continue
                kind = found[0]
            kinds[kind] = kinds.get(kind, 0) + 1
            keep.append(Hidden((float(x0), float(y0), float(x1), float(y1)), now, kind))
        with self._lock:
            self._frame_size = (W, H)
            hold = cfg.strict_hold_s if cfg.strict else cfg.hold_s
            old = [h for h in self._hidden if now - h.last_seen <= hold and
                   not any(_overlap(h.box, k.box) > 0.3 for k in keep)]
            self._hidden = keep + old
        st = self.stats
        st.runs += 1
        st.ms = (time.perf_counter() - t) * 1000
        st.boxes, st.sensitive, st.kinds, st.last_run = len(boxes), sum(v for k, v in kinds.items() if k != "text"), \
            kinds, now

    # ------------------------------------------------------------------ output thread
    def boxes_for(self, W: int, H: int, now: float | None = None) -> list[tuple[int, int, int, int]]:
        """Pixel rectangles to hide in a W x H output frame right now (custom regions + text boxes, padded)."""
        cfg = self.cfg
        if not cfg.enabled:
            return []
        now = time.monotonic() if now is None else now
        out = [(int(x * W), int(y * H), int((x + w) * W + 0.999), int((y + h) * H + 0.999)) for x, y, w, h in cfg.regions]
        with self._lock:
            hidden = list(self._hidden)
            fw, fh = self._frame_size
        stale = (now - self.stats.last_run) > max(2.0 / cfg.fps, 1.0) if self.running or cfg.strict else False
        holding = cfg.strict and (stale or self.stats.state in ("error", "stopped"))
        if cfg.strict:
            self.stats.state = "holding" if holding and self.stats.state != "no_models" else self.stats.state
        hold = cfg.strict_hold_s if cfg.strict else cfg.hold_s
        sx, sy = (W / fw, H / fh) if fw and fh else (1.0, 1.0)
        for h in hidden:
            if now - h.last_seen > hold:
                continue
            x0, y0, x1, y1 = h.box[0] * sx, h.box[1] * sy, h.box[2] * sx, h.box[3] * sy
            pad = (y1 - y0) * (cfg.padding + (cfg.strict_grow if holding else 0.0))
            out.append((max(0, int(x0 - pad)), max(0, int(y0 - pad)), min(W, int(x1 + pad + 0.999)),
                        min(H, int(y1 + pad + 0.999))))
        self.stats.hidden = len(out)
        return out

    def apply(self, bgra: np.ndarray, now: float | None = None) -> np.ndarray:
        """Hide the current boxes in a copy of `bgra` (returns `bgra` itself when nothing is hidden)."""
        h, w = bgra.shape[:2]
        rects = self.boxes_for(w, h, now)
        if not rects:
            return bgra
        out = bgra.copy()
        for x0, y0, x1, y1 in rects:
            if x1 - x0 < 2 or y1 - y0 < 2:
                continue
            roi = out[y0:y1, x0:x1]
            if self.cfg.effect == "black":
                roi[..., :3] = 0
            elif self.cfg.effect == "blur":
                k = max(3, (min(x1 - x0, y1 - y0) // 3) | 1)
                roi[:] = cv2.GaussianBlur(roi, (k, k), 0)
            else:
                bs = max(4, min(x1 - x0, y1 - y0) // 4)
                small = cv2.resize(roi, (max(1, (x1 - x0) // bs), max(1, (y1 - y0) // bs)),
                                   interpolation=cv2.INTER_AREA)
                roi[:] = cv2.resize(small, (x1 - x0, y1 - y0), interpolation=cv2.INTER_NEAREST)
        return out


def _overlap(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return inter / small if small > 0 else 0.0
