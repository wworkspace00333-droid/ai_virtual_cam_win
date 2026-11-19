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
`cooldown_s` without another switch (hysteresis + cooldown: no flapping). Active-speaker switching is not offered:
the app has no reliable per-camera speech signal (one microphone, no direction).
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
MAX_SOURCES = 3                     # extra sources besides the main input
SOURCE_KEYS = ("method", "device_unique_id", "device_name_hint", "screen_target", "video_path", "video_loop")


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
    auto: str = "off"                             # off | people
    fallback: str = ""                            # source used by auto "people" when the main camera is empty
    hold_s: float = 3.0
    cooldown_s: float = 8.0

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
            pick("missing", MISSING, "placeholder"), pick("auto", ("off", "people"), "off"), fallback,
            _f(d.get("hold_s"), 0.5, 60, 3.0), _f(d.get("cooldown_s"), 0, 600, 8.0))

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
    """Hysteresis + cooldown for automatic camera switching by people on the main camera."""

    def __init__(self):
        self.current: str | None = None             # None: the layout's own primary
        self._since: float | None = None
        self._last_switch = -1e9
        self.switches = 0

    def reset(self) -> None:
        self.current, self._since, self._last_switch = None, None, -1e9

    def update(self, cfg: MulticamConfig, people: int, now: float) -> str | None:
        if cfg.auto != "people" or not cfg.fallback:
            self.reset()
            return None
        want = cfg.fallback if people == 0 else None
        if want == self.current:
            self._since = None
            return self.current
        if self._since is None:
            self._since = now
        if now - self._since >= cfg.hold_s and now - self._last_switch >= cfg.cooldown_s:
            self.current, self._since, self._last_switch = want, None, now
            self.switches += 1
            log.info("multicam: auto switch to %s (%d people on the main camera)", want or "main", people)
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


class MulticamService:
    """What the pipeline calls once per output frame, plus manual camera / layout switching."""

    def __init__(self, sources: SourceSet):
        self.sources = sources
        self.auto = AutoSwitcher()
        self.override: str | None = None             # manual camera switch (action "camera_next"), until scene change
        self.layout_override: str | None = None      # manual layout switch (action "layout_next")
        self.stats = MulticamStats()
        self._cfg = MulticamConfig()

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

    def stop(self) -> None:
        self.sources.stop()

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
        auto = self.auto.update(cfg, people, now)
        primary = self.override or auto or cfg.primary
        eff = cfg if not self.layout_override else MulticamConfig(**{**cfg.__dict__, "layout": self.layout_override})
        tiles = layout_tiles(eff, primary)
        if len(tiles) == 1 and tiles[0].source == "main":
            self.stats.primary = "main"
            return out
        t0 = time.perf_counter()
        frames: dict[str, np.ndarray | None] = {"main": out}
        stale = 0
        for t in tiles:
            if t.source in frames:
                continue
            got = self.sources.latest(t.source, hnow, cfg)
            if got is False:
                continue
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
        return res
