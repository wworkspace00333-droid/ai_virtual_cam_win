"""Multi-camera studio (0.6.0 Phase 2): extra sources and layouts on top of the existing capture and output.

Sources. The main input stays what it is (Input page, CaptureSupervisor `ctx.capture`, AI and background run on it).
Each extra source — a camera, the screen or a window, a video file, the test pattern — gets a CaptureSupervisor of
its own (same states, frozen / missing detection and reconnects as the main input) and so a LatestSlot of its own.
Nothing queues: the output thread peeks the newest frame of each source and never waits for one.

Synchronisation. Frames carry host-clock arrival times (vcstudio.core.frame.host_time_s). A secondary frame older
than `max_age_s` is stale: by `missing` policy it is drawn as it is ("hold", the last good picture, up to
`hold_max_s`), replaced by a dark "no signal" tile ("placeholder") or left out ("hide"). The main frame paces the
output; a slow or stalled secondary camera never slows it down.

Layouts (profile / scene section "multicam", so a scene switch changes the layout atomically with the rest):
  single        one source fills the picture: "main" or any extra source (camera switching)
  pip           the primary fills the picture, the others in small windows (corner, size, margin)
  side_by_side  two sources next to each other, letterboxed
  split_v       two sources one above the other
  grid          up to 4 sources in a 2 x 2 grid
  custom        tiles with x, y, w, h in 0..1 of the picture, each with crop, rotation, border, rounded corners and
                opacity
Composition runs on the finished output picture (after regions, background and auto-framing, before plugins), on
the CPU with OpenCV: one resize per visible tile, tile masks cached per size.

Automatic switching (`auto`): "people" switches the single / primary source to `fallback` when nobody has been seen
on the main camera for `hold_s` and back once people are seen again for `hold_s`; a switch is followed by at least
`cooldown_s` without another switch (hysteresis + cooldown: no flapping). "speaker" makes the source whose
speaker's mouth moves clearly the most the single / primary source (vcstudio/ai/speaker.py: mouth movement on each
source, since one microphone gives no per-camera signal); while nobody speaks the picture stays where it is.

Per camera (0.7.0), keys of each extra source: `color_preset` (a Colour Studio preset by name, graded on that
camera's own picture with its own grader before it is drawn) and `privacy`: "shield" (default: the privacy shield
checks it like the main camera when "extra cameras" is on), "hide" (the whole tile is always drawn hidden with the
shield's effect, e.g. a room camera that must never show detail) or "off" (not checked; ignored in strict mode).
Order: colour, then privacy, so nothing graded escapes the shield.

Transitions (`transition`, 0.7.0): when the primary source or the layout changes (automatic, manual or a scene
switch), "cut" shows the new picture at once; "fade" cross-fades from the last composed picture to the new one over
`transition_s`; "slide" pushes the old picture out to the left. The old picture is one kept frame (no source is
read twice): with fade / slide on, each composed picture is copied (about 8 MB at 1080p), and a fade costs one blend
per frame while it runs.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import cv2
import numpy as np

log = logging.getLogger(__name__)

LAYOUTS = ("single", "pip", "side_by_side", "split_v", "grid", "custom")
MISSING = ("hold", "placeholder", "hide")
CORNERS = ("top_right", "top_left", "bottom_right", "bottom_left")
AUTO_MODES = ("off", "people", "speaker")
TRANSITIONS = ("cut", "fade", "slide")
MAX_SOURCES = 3                     # extra sources besides the main input
SOURCE_KEYS = ("method", "device_unique_id", "device_name_hint", "screen_target", "video_path", "video_loop",
               "color_preset", "privacy")
PRIVACY_MODES = ("shield", "hide", "off")


def _f(v, lo: float, hi: float, default: float) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return min(hi, max(lo, x))


@dataclass(frozen=True)
class Tile:
    source: str                                   # "main" or an extra source id
    x: float = 0.0
    y: float = 0.0
    w: float = 1.0
    h: float = 1.0
    crop: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)   # x0, y0, x1, y1 of the source, 0..1
    rotation: int = 0                             # degrees, multiples of 90
    border_px: int = 0
    border_bgr: tuple[int, int, int] = (255, 255, 255)
    radius_px: int = 0
    opacity: float = 1.0
    fit: str = "cover"                            # cover (crop to fill) | contain (letterbox)

    @staticmethod
    def from_dict(d: dict) -> Tile:
        crop = d.get("crop") or (0, 0, 1, 1)
        try:
            x0, y0, x1, y1 = (_f(c, 0, 1, dv) for c, dv in zip(crop, (0, 0, 1, 1)))
        except (TypeError, ValueError):
            x0, y0, x1, y1 = 0.0, 0.0, 1.0, 1.0
        if x1 - x0 < 0.05 or y1 - y0 < 0.05:
            x0, y0, x1, y1 = 0.0, 0.0, 1.0, 1.0
        col = d.get("border_color") or "#ffffff"
        try:
            rgb = int(str(col).lstrip("#"), 16)
            bgr = (rgb & 255, (rgb >> 8) & 255, (rgb >> 16) & 255)
        except ValueError:
            bgr = (255, 255, 255)
        return Tile(str(d.get("source") or "main"), _f(d.get("x"), 0, 1, 0), _f(d.get("y"), 0, 1, 0),
                    _f(d.get("w"), 0.05, 1, 1), _f(d.get("h"), 0.05, 1, 1), (x0, y0, x1, y1),
                    int(round(_f(d.get("rotation"), -360, 360, 0) / 90)) * 90 % 360,
                    int(_f(d.get("border_px"), 0, 40, 0)), bgr, int(_f(d.get("radius_px"), 0, 400, 0)),
                    _f(d.get("opacity"), 0, 1, 1), "contain" if d.get("fit") == "contain" else "cover")


@dataclass(frozen=True)
class MulticamConfig:
    enabled: bool = False
    layout: str = "pip"
    primary: str = "main"                         # source that fills the picture in single / pip
    sources: tuple[dict, ...] = ()                # {"id", "name", "enabled", + SOURCE_KEYS}
    tiles: tuple[Tile, ...] = ()                  # custom layout
    pip_corner: str = "bottom_right"
    pip_size: float = 0.28                        # width of a PiP window, share of the picture width
    pip_margin: float = 0.03
    border_px: int = 2
    radius_px: int = 12
    max_age_s: float = 0.5
    hold_max_s: float = 3.0
    missing: str = "placeholder"
    auto: str = "off"                             # off | people | speaker
    fallback: str = ""                            # source used by auto "people" when the main camera is empty
    hold_s: float = 3.0
    cooldown_s: float = 8.0
    transition: str = "cut"                       # cut | fade | slide
    transition_s: float = 0.4

    @staticmethod
    def from_profile(body: dict) -> MulticamConfig:
        d = body.get("multicam") or {}
        if not isinstance(d, dict):
            return MulticamConfig()
        srcs = []
        for i, s in enumerate(d.get("sources") or []):
            if not isinstance(s, dict) or not s.get("method"):
                continue
            sid = str(s.get("id") or f"cam{i + 1}")
            srcs.append({"id": sid, "name": str(s.get("name") or sid), "enabled": bool(s.get("enabled", True)),
                         **{k: s[k] for k in SOURCE_KEYS if k in s}})
        srcs = srcs[:MAX_SOURCES]
        ids = {"main"} | {s["id"] for s in srcs}
        tiles = tuple(Tile.from_dict(t) for t in (d.get("tiles") or []) if isinstance(t, dict))[:8]
        def pick(key: str, allowed, default: str) -> str:
            v = d.get(key)
            return str(v) if v in allowed else default
        layout = pick("layout", LAYOUTS, "pip")
        primary = pick("primary", ids, "main")
        fallback = pick("fallback", ids - {"main"}, "")
        return MulticamConfig(
            bool(d.get("enabled", False)), layout, primary, tuple(srcs),
            tuple(t for t in tiles if t.source in ids),
            pick("pip_corner", CORNERS, "bottom_right"),
            _f(d.get("pip_size"), 0.1, 0.5, 0.28), _f(d.get("pip_margin"), 0, 0.2, 0.03),
            int(_f(d.get("border_px"), 0, 40, 2)), int(_f(d.get("radius_px"), 0, 200, 12)),
            _f(d.get("max_age_s"), 0.05, 5, 0.5), _f(d.get("hold_max_s"), 0, 60, 3.0),
            pick("missing", MISSING, "placeholder"), pick("auto", AUTO_MODES, "off"), fallback,
            _f(d.get("hold_s"), 0.5, 60, 3.0), _f(d.get("cooldown_s"), 0, 600, 8.0),
            pick("transition", TRANSITIONS, "cut"), _f(d.get("transition_s"), 0.05, 3.0, 0.4))

    @property
    def active(self) -> bool:
        return self.enabled and (any(s["enabled"] for s in self.sources) or self.primary != "main")

    def enabled_ids(self) -> list[str]:
        return [s["id"] for s in self.sources if s["enabled"]]


# ----------------------------------------------------------------------------------------------- layout maths
def layout_tiles(cfg: MulticamConfig, primary: str | None = None) -> list[Tile]:
    """The tiles to draw, back to front, for the configured layout (pure: tested directly)."""
    prim = primary or cfg.primary
    ids = ["main"] + cfg.enabled_ids()
    others = [i for i in ids if i != prim]
    b, r = cfg.border_px, cfg.radius_px
    if cfg.layout == "single":
        return [Tile(prim)]
    if cfg.layout == "pip":
        out = [Tile(prim)]
        s, m = cfg.pip_size, cfg.pip_margin
        h = s                                      # same share of width and height: the picture's own aspect
        for k, sid in enumerate(others[:3]):
            right = cfg.pip_corner.endswith("right")
            bottom = cfg.pip_corner.startswith("bottom")
            x = 1 - m - s if right else m
            y = (1 - m - h - k * (h + m)) if bottom else (m + k * (h + m))
            out.append(Tile(sid, x, max(0.0, y), s, h, border_px=b, radius_px=r))
        return out
    if cfg.layout in ("side_by_side", "split_v"):
        pair = [prim] + others[:1] if others else [prim]
        if len(pair) == 1:
            return [Tile(prim)]
        if cfg.layout == "side_by_side":
            return [Tile(pair[0], 0, 0, 0.5, 1, fit="cover"), Tile(pair[1], 0.5, 0, 0.5, 1, fit="cover")]
        return [Tile(pair[0], 0, 0, 1, 0.5), Tile(pair[1], 0, 0.5, 1, 0.5)]
    if cfg.layout == "grid":
        cells = [prim] + others[:3]
        return [Tile(sid, (k % 2) * 0.5, (k // 2) * 0.5, 0.5, 0.5) for k, sid in enumerate(cells)]
    return list(cfg.tiles) or [Tile(prim)]


_mask_cache: dict[tuple[int, int, int], np.ndarray] = {}


def _rounded_mask(w: int, h: int, r: int) -> np.ndarray:
    key = (w, h, r)
    m = _mask_cache.get(key)
    if m is None:
        m = np.full((h, w), 255, np.uint8)
        r = min(r, w // 2, h // 2)
        if r > 0:
            m[:] = 0
            cv2.rectangle(m, (r, 0), (w - 1 - r, h - 1), 255, -1)
            cv2.rectangle(m, (0, r), (w - 1, h - 1 - r), 255, -1)
            for cx, cy in ((r, r), (w - 1 - r, r), (r, h - 1 - r), (w - 1 - r, h - 1 - r)):
                cv2.circle(m, (cx, cy), r, 255, -1, lineType=cv2.LINE_AA)
        if len(_mask_cache) > 64:
            _mask_cache.clear()
        _mask_cache[key] = m
    return m


def _resize(src: np.ndarray, w: int, h: int) -> np.ndarray:
    """Downscale in two steps when the source is much larger: an integer-factor INTER_AREA (OpenCV's fast path,
    averages every pixel: no aliasing) and a bilinear finish. A general INTER_AREA cost ~2.7 ms for 720p -> 360x200
    on the 2-core server; this costs ~0.6 ms."""
    sh, sw = src.shape[:2]
    f = int(min(sw / max(1, w), sh / max(1, h)))
    if f >= 2:
        src = cv2.resize(src, (sw // f, sh // f), interpolation=cv2.INTER_AREA)
    return cv2.resize(src, (w, h), interpolation=cv2.INTER_LINEAR)


def _fit(src: np.ndarray, w: int, h: int, mode: str) -> np.ndarray:
    sh, sw = src.shape[:2]
    if mode == "contain":
        k = min(w / sw, h / sh)
        nw, nh = max(1, int(sw * k)), max(1, int(sh * k))
        out = np.zeros((h, w, 4), np.uint8)
        out[..., 3] = 255
        x, y = (w - nw) // 2, (h - nh) // 2
        out[y:y + nh, x:x + nw] = _resize(src, nw, nh)
        return out
    k = max(w / sw, h / sh)                        # cover: crop the centre to the tile's aspect
    cw, ch = min(sw, max(1, int(round(w / k)))), min(sh, max(1, int(round(h / k))))
    x0, y0 = (sw - cw) // 2, (sh - ch) // 2
    return _resize(src[y0:y0 + ch, x0:x0 + cw], w, h)       # a view: OpenCV takes row-strided input


def placeholder(w: int, h: int, text: str = "no signal") -> np.ndarray:
    out = np.zeros((h, w, 4), np.uint8)
    out[..., :3] = 28
    out[..., 3] = 255
    scale = max(0.4, min(w, h) / 360)
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
    cv2.putText(out, text, ((w - tw) // 2, (h + th) // 2), cv2.FONT_HERSHEY_SIMPLEX, scale, (160, 160, 160, 255), 1,
                cv2.LINE_AA)
    return out


def compose(canvas_wh: tuple[int, int], tiles: list[Tile], frames: dict[str, np.ndarray | None],
            base: np.ndarray | None = None) -> np.ndarray:
    """Draw `tiles` (back to front) from `frames` (BGRA arrays; None = draw a placeholder; missing key = skip)."""
    W, H = canvas_wh
    out = base.copy() if base is not None and base.shape[1] == W and base.shape[0] == H else None
    if out is None:
        out = np.zeros((H, W, 4), np.uint8)
        out[..., 3] = 255
    for t in tiles:
        if t.source not in frames:
            continue
        x, y = int(round(t.x * W)), int(round(t.y * H))
        w, h = int(round(t.w * W)), int(round(t.h * H))
        w, h = min(w, W - x), min(h, H - y)
        if w < 8 or h < 8:
            continue
        src = frames[t.source]
        if src is None:
            tile = placeholder(w, h)
        else:
            sh, sw = src.shape[:2]
            x0, y0, x1, y1 = t.crop
            if (x0, y0, x1, y1) != (0.0, 0.0, 1.0, 1.0):
                src = src[int(y0 * sh):max(int(y0 * sh) + 1, int(y1 * sh)),
                          int(x0 * sw):max(int(x0 * sw) + 1, int(x1 * sw))]
            if t.rotation:
                src = np.ascontiguousarray(np.rot90(src, k=-(t.rotation // 90)))
            tile = _fit(src, w, h, t.fit)
        if t.border_px:
            b = t.border_px
            tile = tile.copy()
            cv2.rectangle(tile, (b // 2, b // 2), (w - 1 - b // 2, h - 1 - b // 2), (*t.border_bgr, 255), b)
        roi = out[y:y + h, x:x + w]
        if t.opacity < 0.999:                      # uniform opacity: one weighted sum in uint8 (no float frame)
            tile = cv2.addWeighted(tile, t.opacity, roi, 1.0 - t.opacity, 0.0)
        if t.radius_px:
            np.copyto(roi, tile, where=_rounded_mask(w, h, t.radius_px)[..., None] > 127)
        else:
            roi[:] = tile
        out[y:y + h, x:x + w, 3] = 255
    return out


# ----------------------------------------------------------------------------------------------- sources
class SourceSet:
    """One CaptureSupervisor per extra source; started and stopped to match the scene's source list."""

    def __init__(self, factory_for: Callable[[dict], Callable], event_sink=None,
                 refuse: Callable[[dict], str] | None = None):
        self.factory_for = factory_for              # source spec -> capture source factory (AppContext.factory_for)
        self.refuse = refuse or (lambda spec: "")    # non-empty reason: never open this source (feedback loop)
        self._sink = event_sink or (lambda lv, c, m: None)
        self._sups: dict[str, Any] = {}
        self._specs: dict[str, dict] = {}
        self._held: dict[str, tuple[np.ndarray, float, int]] = {}
        self.errors: dict[str, str] = {}
        self._lock = threading.Lock()

    def sync(self, specs: list[dict]) -> None:
        """Start sources that are new or changed, stop the ones no longer listed."""
        from vcstudio.core.capture_supervisor import CaptureSupervisor
        want = {s["id"]: s for s in specs if s.get("enabled", True)}
        with self._lock:
            for sid in list(self._sups):
                if sid not in want or self._specs.get(sid) != want[sid]:
                    self._sups.pop(sid).stop()
                    self._specs.pop(sid, None)
                    self._held.pop(sid, None)
            for sid, spec in want.items():
                if sid in self._sups:
                    continue
                why = self.refuse(spec)
                if why:
                    self.errors[sid] = why
                    log.error("multicam: source %s refused: %s", sid, why)
                    continue
                try:
                    factory = self.factory_for(spec)
                except Exception as e:              # noqa: BLE001 - bad spec: report, keep the others
                    self.errors[sid] = str(e)
                    continue
                def sink(lv: str, _c: str, m: str, sid: str = sid) -> None:
                    self._sink(lv, f"multicam:{sid}", m)
                sup = CaptureSupervisor(event_sink=sink)
                sup.start(factory)
                self._sups[sid], self._specs[sid] = sup, dict(spec)
                self.errors.pop(sid, None)
                log.info("multicam: source %s started (%s)", sid, spec.get("method"))

    def stop(self) -> None:
        with self._lock:
            for sup in self._sups.values():
                sup.stop()
            self._sups.clear()
            self._specs.clear()
            self._held.clear()

    def ids(self) -> list[str]:
        with self._lock:
            return list(self._sups)

    def frames(self) -> dict:
        """{source id: newest Frame or None} of the running extra sources (for analysers; no copy is made)."""
        with self._lock:
            sups = dict(self._sups)
        return {sid: sup.slot.peek()[0] for sid, sup in sups.items()}

    def stats(self) -> dict[str, Any]:
        with self._lock:
            sups = dict(self._sups)
        return {sid: sup.stats() for sid, sup in sups.items()}

    def latest(self, sid: str, now: float, cfg: MulticamConfig) -> np.ndarray | None | bool:
        """BGRA array of the newest usable frame; None = draw a placeholder; False = leave the tile out."""
        with self._lock:
            sup = self._sups.get(sid)
        if sup is None:
            return None if cfg.missing == "placeholder" else False
        frame, ver = sup.slot.peek()
        held = self._held.get(sid)
        if frame is not None and now - frame.arrival <= cfg.max_age_s:
            if held is not None and held[2] == ver:            # same frame as last output frame: no new copy
                return held[0]
            with frame.bgra() as a:
                arr = np.array(a, copy=True)                   # sources may reuse their buffers
            self._held[sid] = (arr, frame.arrival, ver)
            return arr
        if cfg.missing == "hold" and held is not None and now - held[1] <= cfg.hold_max_s:
            return held[0]
        return None if cfg.missing in ("placeholder", "hold") else False


class AutoSwitcher:
    """Hysteresis + cooldown for automatic camera switching: by people on the main camera, or by who speaks."""

    def __init__(self):
        self.current: str | None = None             # None: the layout's own primary
        self._since: float | None = None
        self._last_switch = -1e9
        self.switches = 0

    def reset(self) -> None:
        self.current, self._since, self._last_switch = None, None, -1e9

    def update(self, cfg: MulticamConfig, people: int, now: float, speaker: str | None = None) -> str | None:
        if cfg.auto == "speaker":
            if speaker is None or speaker == (self.current or cfg.primary):
                self._since = None                  # nobody speaks / the speaker is shown: stay
                return self.current
            want: str | None = None if speaker == cfg.primary else speaker
        elif cfg.auto != "people" or not cfg.fallback:
            self.reset()
            return None
        else:
            want = cfg.fallback if people == 0 else None
        if want == self.current:
            self._since = None
            return self.current
        if self._since is None:
            self._since = now
        if now - self._since >= cfg.hold_s and now - self._last_switch >= cfg.cooldown_s:
            self.current, self._since, self._last_switch = want, None, now
            self.switches += 1
            log.info("multicam: auto switch to %s (%s)", want or cfg.primary,
                     f"speaker on {speaker}" if cfg.auto == "speaker" else f"{people} people on the main camera")
        return self.current


@dataclass
class MulticamStats:
    composed: int = 0
    compose_ms: float = 0.0
    stale_tiles: int = 0
    errors: int = 0
    last_error: str = ""
    primary: str = "main"
    sources: dict = field(default_factory=dict)
    transitions: int = 0


def transition_frame(old: np.ndarray, new: np.ndarray, kind: str, p: float) -> np.ndarray:
    """The picture `p` (0..1) of the way from `old` to `new` (pure: tested)."""
    p = min(1.0, max(0.0, p))
    if p >= 1.0 or old.shape != new.shape:
        return new
    if kind == "slide":
        w = new.shape[1]
        k = int(round(w * p))
        out = np.empty_like(new)
        out[:, : w - k] = old[:, k:]
        out[:, w - k:] = new[:, : k] if k else new[:, :0]
        return out
    return cv2.addWeighted(old, 1.0 - p, new, p, 0.0)


class MulticamService:
    """What the pipeline calls once per output frame, plus manual camera / layout switching."""

    def __init__(self, sources: SourceSet):
        self.sources = sources
        self.auto = AutoSwitcher()
        self.override: str | None = None             # manual camera switch (action "camera_next"), until scene change
        self.speaker = None                          # vcstudio.ai.speaker.SpeakerService (auto "speaker"), set by the app
        self.layout_override: str | None = None      # manual layout switch (action "layout_next")
        # callable(source id, BGRA, privacy mode) -> BGRA drawn instead (the privacy shield on extra cameras), set by
        # the app; if it fails the tile shows the placeholder, never the unfiltered picture
        self.tile_filter: Callable[[str, np.ndarray, str], np.ndarray] | None = None
        # callable(preset name) -> ColorConfig | None (Colour Studio presets), set by the app
        self.color_for: Callable[[str], Any] | None = None
        self._graders: dict[str, Any] = {}
        self.stats = MulticamStats()
        self._cfg = MulticamConfig()
        self._last_key: tuple | None = None         # (primary, layout) of the last composed picture
        self._last_out: np.ndarray | None = None
        self._trans: tuple | None = None            # (old picture, start time, kind, duration)

    def apply(self, cfg: MulticamConfig | None, running: bool) -> None:
        """A scene was published: start / stop its sources (only while capture runs)."""
        cfg = cfg or MulticamConfig()
        if cfg != self._cfg:
            self.override = self.layout_override = None
            self.auto.reset()
        self._cfg = cfg
        if running and cfg.enabled:
            self.sources.sync(list(cfg.sources))
        else:
            self.sources.stop()
        if self.speaker is not None:
            if running and cfg.enabled and cfg.auto == "speaker" and cfg.enabled_ids():
                self.speaker.start()
            else:
                self.speaker.stop()

    def stop(self) -> None:
        self.sources.stop()
        if self.speaker is not None:
            self.speaker.stop()

    def next_camera(self) -> str:
        ids = ["main"] + self._cfg.enabled_ids()
        cur = self.override or self._cfg.primary
        self.override = ids[(ids.index(cur) + 1) % len(ids)] if cur in ids else "main"
        return self.override

    def next_layout(self) -> str:
        cur = self.layout_override or self._cfg.layout
        opts = [x for x in LAYOUTS if x != "custom" or self._cfg.tiles]
        self.layout_override = opts[(opts.index(cur) + 1) % len(opts)] if cur in opts else "pip"
        return self.layout_override

    def process(self, out: np.ndarray, cfg: MulticamConfig, people: int, now: float | None = None) -> np.ndarray:
        """Compose this output frame; returns `out` itself when nothing is to be drawn."""
        if not cfg.enabled:
            return out
        now = time.monotonic() if now is None else now
        from vcstudio.core.frame import host_time_s
        hnow = host_time_s()
        spk = self.speaker.speaker() if self.speaker is not None and cfg.auto == "speaker" else None
        auto = self.auto.update(cfg, people, now, spk)
        primary = self.override or auto or cfg.primary
        eff = cfg if not self.layout_override else MulticamConfig(**{**cfg.__dict__, "layout": self.layout_override})
        tiles = layout_tiles(eff, primary)
        key = (primary, eff.layout)
        if self._last_key is not None and key != self._last_key and cfg.transition != "cut" and \
                self._last_out is not None:
            self._trans = (self._last_out, now, cfg.transition, cfg.transition_s)
            self.stats.transitions += 1
        self._last_key = key
        if len(tiles) == 1 and tiles[0].source == "main":
            self.stats.primary = "main"
            return self._transition(out, now, cfg)
        t0 = time.perf_counter()
        frames: dict[str, np.ndarray | None] = {"main": out}
        specs = {s["id"]: s for s in cfg.sources}
        for sid in [k for k in self._graders if k not in specs]:
            del self._graders[sid]
        stale = 0
        for t in tiles:
            if t.source in frames:
                continue
            got = self.sources.latest(t.source, hnow, cfg)
            if got is False:
                continue
            spec = specs.get(t.source, {})
            if got is not None and got is not True and spec.get("color_preset") and self.color_for is not None:
                got = self._grade(t.source, got, str(spec["color_preset"]), hnow)
            if got is not None and got is not True and self.tile_filter is not None:
                try:
                    mode = str(spec.get("privacy")) if spec.get("privacy") in PRIVACY_MODES else "shield"
                    got = self.tile_filter(t.source, got, mode)
                except Exception:
                    log.exception("multicam: tile filter failed for %s; placeholder shown", t.source)
                    got = None
            if got is None or got is True:
                stale += 1
                frames[t.source] = None
            else:
                frames[t.source] = got
        h, w = out.shape[:2]
        base = out if tiles[0].source == "main" and (tiles[0].w, tiles[0].h) == (1.0, 1.0) else None
        res = compose((w, h), tiles[1:] if base is not None else tiles, frames, base)
        ms = (time.perf_counter() - t0) * 1000
        s = self.stats
        s.composed += 1
        s.compose_ms = ms if s.composed == 1 else 0.9 * s.compose_ms + 0.1 * ms
        s.stale_tiles = stale
        s.primary = primary
        return self._transition(res, now, cfg)

    def _grade(self, sid: str, img: np.ndarray, preset: str, t: float) -> np.ndarray:
        """This camera's colour preset on its own picture; any failure draws it ungraded."""
        try:
            ccfg = self.color_for(preset) if self.color_for is not None else None
            if ccfg is None or not ccfg.active:
                return img
            g = self._graders.get(sid)
            if g is None:
                from vcstudio.replacement.color import ColorGrader
                g = self._graders[sid] = ColorGrader()
            return g.apply(img, ccfg, t)
        except Exception:
            log.exception("multicam: colour preset %r failed on %s", preset, sid)
            return img

    def _transition(self, res: np.ndarray, now: float, cfg: MulticamConfig) -> np.ndarray:
        """Keep a copy of this picture as the start of a future transition (only when transitions are on: the
        output buffer may be reused); blend while one runs."""
        tr = self._trans
        if tr is not None:
            old, t0, kind, dur = tr
            p = (now - t0) / max(1e-3, dur)
            if p >= 1.0 or old.shape != res.shape:
                self._trans = None
            else:
                res = transition_frame(old, res, kind, p)
        self._last_out = None if cfg.transition == "cut" else np.array(res, copy=True)
        return res
