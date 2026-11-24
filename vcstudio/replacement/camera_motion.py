"""AI handheld camera motion (0.7.0): the output moves as if an operator held the camera (profile section
"camera_motion").

The picture is moved as one rigid image — shift, a small roll and a slight zoom (a similarity transform: angles and
proportions are kept, nothing is bent or stretched) — with a fixed overscan so no border ever shows. The movement is
not random coordinates per frame; per channel (x, y, roll, zoom) it is built from:
  drift        slow wandering: fractal 1-D gradient (Perlin) noise, 3 octaves, at the mode's drift frequency
  micro-jitter small fast tremor of the hands: 2 octaves of the same noise at `frequency` Hz
  variability  a very slow noise envelope on the amplitude (`randomness`): calm and busier stretches alternate
  inertia      the sum drives a damped spring per channel (natural frequency from `smoothness`, damping ratio from
               the mode): the camera has mass, overshoots a little when it changes direction and settles naturally
  roll         partly coupled to the sideways movement, as a real wrist turns while the arm moves
The noise lattice is hashed with 64-bit integers and its octaves use a non-integer lacunarity (2.137), so the motion
has no period: no short loop ever repeats (loop prevention). `seed` changes the whole character. The time base is
the frame's presentation time, so the motion is identical on every output and independent of the frame rate.

Modes: natural, micro, professional, documentary, product (a slow arc around the subject: sideways pan with a
counter-roll and zoom breathing, plus a little drift) and cinematic. PRESETS hold complete settings; the user saves
their own in settings "motion_presets".

Object-aware (detection): the largest tracked object (things before people and faces) becomes the pivot of roll and
zoom, so it turns in place instead of swinging out, and the movement is scaled down whenever it would push the object
closer than 3 % to the frame edge. The object is never warped on its own: object and surroundings move together as
one picture (moving them apart would need the hidden background to be invented; not done). Without detection the
whole frame moves around its centre.

Auto intensity: a 1/16-size grey copy of each input frame is compared with the previous one; when the picture is
already moving (a real handheld camera, panning, lots of movement in view) the artificial motion is reduced to as
little as 30 %. Stabilisation (0..1) removes most of the micro-jitter and half of the drift.

Cost: one cv2.warpAffine per frame into a new image (the only pass over the pixels; no extra copy). Measured on the
2-core development server, whole step with auto intensity and an object: 1080p 5.9 ms linear / 26 ms cubic, 720p
3.0 ms linear / 11.5 ms cubic. Quality "auto" uses cubic (sharper detail) up to 1280x720 and linear above, and falls
back to linear when the step takes longer than BUDGET_MS on average.
When OpenCV reports an OpenCL device the warp runs there (cv2.UMat; GPU through the graphics driver), else on the
processor — DirectML is used for AI models only, an image warp does not go through it. This server has no OpenCL
device, so the OpenCL path is not exercised by the tests here.
"""
from __future__ import annotations

import collections
import math
import time
from dataclasses import asdict, dataclass, fields

import cv2
import numpy as np

MODES = ("natural", "micro", "professional", "documentary", "product", "cinematic")
QUALITIES = ("auto", "linear", "cubic")
MAX_SHIFT = 0.025          # share of the frame width / height moved at intensity 1 and horizontal / vertical 1
MAX_ROT_DEG = 1.5          # roll at rotation 1
MAX_ZOOM = 0.03            # zoom breathing at zoom 1
EDGE_MARGIN = 0.03         # object-aware: the object stays at least this share of the frame from the edges
BUDGET_MS = 12.0           # quality auto: slower than this on average -> linear interpolation
FADE_S = 0.4               # switching on / off eases the motion in / out over this time

# mode -> (drift frequency Hz, micro-jitter share, damping ratio, roll coupling to sideways movement)
MODE_SHAPE = {
    "natural": (0.22, 0.30, 0.62, 0.35),
    "micro": (0.16, 0.45, 0.70, 0.20),
    "professional": (0.12, 0.08, 0.90, 0.25),
    "documentary": (0.33, 0.40, 0.52, 0.45),
    "product": (0.10, 0.06, 0.95, 0.0),
    "cinematic": (0.08, 0.03, 1.00, 0.30),
}

PRESETS: dict[str, dict] = {
    "almost_static": {"mode": "micro", "intensity": 0.15, "horizontal": 0.5, "vertical": 0.5, "rotation": 0.2,
                      "zoom": 0.1, "speed": 0.7, "smoothness": 0.8, "randomness": 0.3, "frequency": 6.0},
    "natural_handheld": {"mode": "natural", "intensity": 0.45, "horizontal": 0.7, "vertical": 0.6, "rotation": 0.4,
                         "zoom": 0.25, "speed": 1.0, "smoothness": 0.6, "randomness": 0.5, "frequency": 7.0},
    "smartphone": {"mode": "natural", "intensity": 0.6, "horizontal": 0.8, "vertical": 0.8, "rotation": 0.6,
                   "zoom": 0.2, "speed": 1.3, "smoothness": 0.35, "randomness": 0.6, "frequency": 9.0},
    "dslr": {"mode": "professional", "intensity": 0.4, "horizontal": 0.7, "vertical": 0.5, "rotation": 0.3,
             "zoom": 0.2, "speed": 0.8, "smoothness": 0.8, "randomness": 0.4, "frequency": 5.0},
    "cinematic_handheld": {"mode": "cinematic", "intensity": 0.5, "horizontal": 0.8, "vertical": 0.5,
                           "rotation": 0.5, "zoom": 0.35, "speed": 0.6, "smoothness": 0.9, "randomness": 0.4,
                           "frequency": 4.0},
    "product_video": {"mode": "product", "intensity": 0.5, "horizontal": 0.8, "vertical": 0.3, "rotation": 0.4,
                      "zoom": 0.5, "speed": 0.8, "smoothness": 0.85, "randomness": 0.3, "frequency": 4.0},
    "macro_product": {"mode": "product", "intensity": 0.25, "horizontal": 0.6, "vertical": 0.3, "rotation": 0.2,
                      "zoom": 0.4, "speed": 0.5, "smoothness": 0.9, "randomness": 0.2, "frequency": 3.0},
    "documentary": {"mode": "documentary", "intensity": 0.7, "horizontal": 0.8, "vertical": 0.7, "rotation": 0.6,
                    "zoom": 0.3, "speed": 1.2, "smoothness": 0.45, "randomness": 0.7, "frequency": 8.0},
}
PRESET_LABELS = {"almost_static": "Almost Static", "natural_handheld": "Natural Handheld",
                 "smartphone": "Smartphone Recording", "dslr": "Professional DSLR",
                 "cinematic_handheld": "Cinematic Handheld", "product_video": "Product Video",
                 "macro_product": "Macro Product Recording", "documentary": "Documentary Camera"}
MODE_LABELS = {"natural": "Natural Handheld", "micro": "Micro Movement", "professional": "Professional Camera",
               "documentary": "Documentary", "product": "Product Showcase", "cinematic": "Subtle Cinematic"}
# (field, lo, hi): numeric settings and their ranges
RANGES = {"intensity": (0.0, 1.0), "horizontal": (0.0, 1.0), "vertical": (0.0, 1.0), "rotation": (0.0, 1.0),
          "zoom": (0.0, 1.0), "speed": (0.1, 3.0), "smoothness": (0.0, 1.0), "randomness": (0.0, 1.0),
          "frequency": (1.0, 15.0), "stabilization": (0.0, 1.0)}
THINGS_FIRST = ("person", "face")      # object-aware: these are pivots only when nothing else is tracked


@dataclass(frozen=True)
class MotionConfig:
    enabled: bool = False
    mode: str = "natural"
    intensity: float = 0.45
    horizontal: float = 0.7
    vertical: float = 0.6
    rotation: float = 0.4
    zoom: float = 0.25
    speed: float = 1.0
    smoothness: float = 0.6
    randomness: float = 0.5
    frequency: float = 7.0
    stabilization: float = 0.0
    seed: int = 1
    auto_intensity: bool = True
    object_aware: bool = True
    quality: str = "auto"

    @classmethod
    def from_dict(cls, d: dict | None) -> "MotionConfig":
        d = d if isinstance(d, dict) else {}
        kw: dict = {}
        for k, (lo, hi) in RANGES.items():
            try:
                kw[k] = float(min(hi, max(lo, float(d.get(k, getattr(cls, k))))))
            except (TypeError, ValueError):
                kw[k] = getattr(cls, k)
        try:
            kw["seed"] = int(d.get("seed", 1)) % 100000
        except (TypeError, ValueError):
            kw["seed"] = 1
        return cls(enabled=bool(d.get("enabled", False)), mode=d.get("mode") if d.get("mode") in MODES else "natural",
                   auto_intensity=bool(d.get("auto_intensity", True)), object_aware=bool(d.get("object_aware", True)),
                   quality=d.get("quality") if d.get("quality") in QUALITIES else "auto", **kw)

    @classmethod
    def from_profile(cls, body: dict) -> "MotionConfig":
        return cls.from_dict(body.get("camera_motion"))

    def to_dict(self) -> dict:
        return asdict(self)

    def preset_values(self) -> dict:
        """The settings a preset stores (everything but on / off)."""
        return {f.name: getattr(self, f.name) for f in fields(self) if f.name != "enabled"}


# --------------------------------------------------------------------------- noise
_M64 = (1 << 64) - 1


def _grad(i: int, seed: int) -> float:
    """Gradient in -1..1 for lattice point i (splitmix64 hash: no period over any practical time)."""
    h = (i * 0x9E3779B97F4A7C15 + (seed + 1) * 0xD1B54A32D192ED03) & _M64
    h ^= h >> 30
    h = (h * 0xBF58476D1CE4E5B9) & _M64
    h ^= h >> 27
    h = (h * 0x94D049BB133111EB) & _M64
    h ^= h >> 31
    return h / 2.0 ** 63 - 1.0


def perlin(x: float, seed: int) -> float:
    """1-D gradient noise, about -1..1, smooth (C2) everywhere (pure: tested)."""
    i = math.floor(x)
    f = x - i
    g0 = _grad(i, seed) * f
    g1 = _grad(i + 1, seed) * (f - 1.0)
    u = f * f * f * (f * (f * 6.0 - 15.0) + 10.0)
    return 2.0 * (g0 + (g1 - g0) * u)


def fbm(x: float, seed: int, octaves: int, lacunarity: float = 2.137, gain: float = 0.5) -> float:
    """Fractal sum of `octaves` noise layers, normalised to about -1..1 (pure)."""
    s, a, norm, f = 0.0, 1.0, 0.0, 1.0
    for o in range(octaves):
        s += a * perlin(x * f + o * 17.31, seed + o * 1013)
        norm += a
        a *= gain
        f *= lacunarity
    return s / norm


# --------------------------------------------------------------------------- motion
@dataclass
class Pose:
    tx: float = 0.0            # shift, share of the frame width
    ty: float = 0.0            # shift, share of the frame height
    rot: float = 0.0           # roll, degrees
    zoom: float = 0.0          # breathing, share (0.01 = 1 % larger)


def target_pose(cfg: MotionConfig, t: float) -> Pose:
    """The pose the operator's hands aim for at time t, before inertia (pure: tested)."""
    drift_hz, jit_share, _z, couple = MODE_SHAPE[cfg.mode]
    s = cfg.seed * 7919
    amp = cfg.intensity
    stab = cfg.stabilization
    env = 1.0 + cfg.randomness * 0.6 * fbm(t * 0.04 * cfg.speed, s + 900, 2)    # calm and busier stretches
    jitter_amp = jit_share * (1.0 - stab) * (0.5 + cfg.randomness)
    drift_amp = 1.0 - 0.5 * stab
    td = t * drift_hz * cfg.speed
    tj = t * cfg.frequency
    oct_d = 2 + int(round(cfg.randomness * 2))

    def chan(k: int) -> float:
        return drift_amp * fbm(td, s + k * 101, oct_d) + jitter_amp * fbm(tj, s + k * 101 + 50, 2)
    ax, ay = MAX_SHIFT * cfg.horizontal * amp * env, MAX_SHIFT * cfg.vertical * amp * env
    ar, az = MAX_ROT_DEG * cfg.rotation * amp * env, MAX_ZOOM * cfg.zoom * amp
    if cfg.mode == "product":
        # a slow arc around the subject: pan with a counter-roll, gentle rise and fall, zoom breathing
        w = 2 * math.pi * 0.07 * cfg.speed
        ph = (cfg.seed % 997) * 0.37
        arc = math.sin(w * t + ph)
        noise = cfg.randomness * 0.35
        x = 0.9 * arc + noise * chan(0)
        y = 0.45 * math.sin(2 * w * t + ph * 1.7) + noise * chan(1)
        r = -0.8 * arc + noise * chan(2)
        z = 0.5 + 0.5 * math.sin(0.5 * w * t + ph * 0.6) + noise * 0.5 * chan(3)
        return Pose(ax * x, ay * y, ar * r, az * z)
    x, y = chan(0), chan(1)
    r = (1 - couple) * chan(2) + couple * x
    z = 0.5 + 0.5 * fbm(t * drift_hz * 0.5 * cfg.speed, s + 404, 2)
    return Pose(ax * x, ay * y, ar * r, az * z)


class Spring:
    """Damped second-order follower: x'' = w^2 (target - x) - 2 zeta w x' (semi-implicit Euler, sub-stepped)."""

    def __init__(self) -> None:
        self.x: float | None = None
        self.v = 0.0

    def step(self, target: float, dt: float, hz: float, zeta: float) -> float:
        if self.x is None:
            self.x = target
            return target
        w = 2 * math.pi * hz
        n = max(1, int(math.ceil(dt * 240)))
        h = dt / n
        for _ in range(n):
            self.v += (w * w * (target - self.x) - 2 * zeta * w * self.v) * h
            self.x += self.v * h
        return self.x


def cover_scale(w: int, h: int, pivot, tx: float, ty: float, rot_deg: float) -> float:
    """Smallest scale at which the moved picture still covers the whole output (pure: tested)."""
    a = math.radians(rot_deg)
    ca, sa = math.cos(a), math.sin(a)
    px, py = pivot
    w, h = w - 1, h - 1                       # pixel centres: interpolation must stay inside 0..w-1, 0..h-1
    need = 1.0
    for cx, cy in ((0, 0), (w, 0), (0, h), (w, h)):
        dx, dy = cx - px - tx, cy - py - ty
        vx, vy = ca * dx - sa * dy, sa * dx + ca * dy             # undo the roll (cv2 rotation matrix inverse)
        need = max(need, vx / max(1e-6, w - px) if vx > 0 else -vx / max(1e-6, px),
                   vy / max(1e-6, h - py) if vy > 0 else -vy / max(1e-6, py))
    return need


def matrix(w: int, h: int, pose: Pose, pivot, base: float) -> tuple[np.ndarray, float]:
    """2x3 forward matrix for a pose around `pivot` (pixels) and the scale used (pure)."""
    tx, ty = pose.tx * w, pose.ty * h
    s = max(base * (1.0 + pose.zoom), cover_scale(w, h, pivot, tx, ty, pose.rot) * 1.002)
    m = cv2.getRotationMatrix2D((float(pivot[0]), float(pivot[1])), pose.rot, s)
    m[0, 2] += tx
    m[1, 2] += ty
    return m, s


def simulate(cfg: MotionConfig, seconds: float = 6.0, fps: float = 30.0, t0: float = 0.0) -> list[Pose]:
    """The poses the engine would produce (with inertia), for the preview path and tests (pure)."""
    eng = HandheldMotion()
    on = MotionConfig.from_dict({**cfg.to_dict(), "enabled": True})
    eng.level = 1.0
    return [eng.pose(on, t0 + i / fps) for i in range(int(seconds * fps))]


@dataclass
class MotionStats:
    ms: float = 0.0
    backend: str = "—"
    interpolation: str = "—"
    auto_factor: float = 1.0
    target: str = ""                 # label of the object the motion is aimed at ("" = whole frame)
    scale: float = 1.0
    error: str = ""


class HandheldMotion:
    """Per-output state; driven once per frame by the pipeline."""

    def __init__(self) -> None:
        self.springs = [Spring() for _ in range(4)]
        self.level = 0.0                     # 0 off .. 1 on (eases in / out over FADE_S)
        self._t: float | None = None
        self._base: float | None = None      # overscan, eased when the settings change
        self._box: tuple[float, float, float, float] | None = None
        self._box_seen = 0.0
        self._prev_small: np.ndarray | None = None
        self._auto = 1.0
        self.stats = MotionStats()
        self.history: collections.deque = collections.deque(maxlen=120)   # recent (tx, ty) for the live trace
        self.last_matrix: np.ndarray | None = None                          # 2x3 transform of the last frame

    @property
    def active(self) -> bool:
        return self.level > 0.0

    def reset(self) -> None:
        self.__init__()

    def _dt(self, t: float) -> float:
        dt = 0.0 if self._t is None else t - self._t
        self._t = t
        return min(0.1, max(0.0, dt))

    def pose(self, cfg: MotionConfig, t: float, factor: float = 1.0) -> Pose:
        """The pose at t after inertia and damping, scaled by the on / off level and `factor` (pure per state)."""
        dt = self._dt(t)
        if dt > 0:
            step = dt / FADE_S
            self.level = min(1.0, self.level + step) if cfg.enabled else max(0.0, self.level - step)
        tp = target_pose(cfg, t)
        _hz, _j, zeta, _c = MODE_SHAPE[cfg.mode]
        hz = 14.0 - 11.0 * cfg.smoothness                 # how quickly the camera's mass follows the hands
        lv = self.level * self.level * (3 - 2 * self.level) * factor
        vals = [sp.step(v, dt, hz, zeta) * lv
                for sp, v in zip(self.springs, (tp.tx, tp.ty, tp.rot, tp.zoom))]
        return Pose(*vals)

    def base_scale(self, cfg: MotionConfig, w: int, h: int, dt: float) -> float:
        """Fixed overscan for the configured amplitudes (worst case around the centre), eased on changes."""
        amp = cfg.intensity * 1.6
        want = cover_scale(w, h, (w / 2, h / 2), MAX_SHIFT * cfg.horizontal * amp * w,
                           MAX_SHIFT * cfg.vertical * amp * h, MAX_ROT_DEG * cfg.rotation * amp)
        want = 1.0 + (want - 1.0) * self.level
        if self._base is None or dt <= 0:
            self._base = want
        else:
            self._base += (want - self._base) * (1 - math.exp(-dt / 0.5))
        return self._base

    # ------------------------------------------------------------------ AI parts
    def _auto_factor(self, img: np.ndarray, dt: float) -> float:
        small = cv2.cvtColor(np.ascontiguousarray(img[::16, ::16, :3]), cv2.COLOR_BGR2GRAY)
        prev, self._prev_small = self._prev_small, small
        if prev is None or prev.shape != small.shape or dt <= 0:
            return self._auto
        d = float(cv2.absdiff(prev, small).mean()) / 255.0
        want = min(1.0, max(0.3, 1.0 - (d - 0.02) / 0.08 * 0.7))
        self._auto += (want - self._auto) * (1 - math.exp(-dt / 1.0))
        return self._auto

    def _object(self, tracked, t: float) -> tuple[float, float, float, float] | None:
        boxes = [(o.label, (o.x0, o.y0, o.x1, o.y1)) for o in tracked or []]
        things = [b for b in boxes if b[0] not in THINGS_FIRST] or boxes
        if things:
            label, box = max(things, key=lambda b: (b[1][2] - b[1][0]) * (b[1][3] - b[1][1]))
            self.stats.target = label
            if self._box is None:
                self._box = box
            else:                                             # smooth: detection jitter must not shake the pivot
                k = 0.15
                self._box = tuple(a + (b - a) * k for a, b in zip(self._box, box))  # type: ignore[assignment]
            self._box_seen = t
        elif self._box is not None and t - self._box_seen > 1.5:
            self._box = None
            self.stats.target = ""
        return self._box

    @staticmethod
    def _keep_inside(m_of, box, w: int, h: int) -> float:
        """Largest k in 0..1 such that the motion scaled by k keeps the object EDGE_MARGIN from the edges."""
        x0, y0, x1, y1 = box
        pts = np.array([[x0, y0, 1], [x1, y0, 1], [x0, y1, 1], [x1, y1, 1]], np.float64)
        mx, my = EDGE_MARGIN * w, EDGE_MARGIN * h

        def ok(k: float) -> bool:
            q = pts @ m_of(k).T
            return bool((q[:, 0] >= mx - 0.5).all() and (q[:, 0] <= w - mx + 0.5).all()
                        and (q[:, 1] >= my - 0.5).all() and (q[:, 1] <= h - my + 0.5).all())
        if ok(1.0):
            return 1.0
        if not ok(0.0):            # the object is already at the edge: move as little as possible
            return 0.0
        lo, hi = 0.0, 1.0
        for _ in range(8):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if ok(mid) else (lo, mid)
        return lo

    # ------------------------------------------------------------------ frame
    def process(self, img: np.ndarray, cfg: MotionConfig, t: float, tracked=None) -> np.ndarray:
        """The moved picture (a new image), or `img` itself when the motion is off and has faded out."""
        if not cfg.enabled and self.level <= 0.0:
            self._t = t
            return img
        t0 = time.perf_counter()
        h, w = img.shape[:2]
        dt = (t - self._t) if self._t is not None else 0.0
        dt = min(0.1, max(0.0, dt))
        factor = self._auto_factor(img, dt) if cfg.auto_intensity else 1.0
        self.stats.auto_factor = factor
        pose = self.pose(cfg, t, factor)
        if self.level <= 0.0:
            return img
        box = self._object(tracked, t) if cfg.object_aware else None
        if not cfg.object_aware:
            self.stats.target = ""
        pivot = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2) if box is not None else (w / 2, h / 2)
        base = self.base_scale(cfg, w, h, dt)
        if box is not None:
            def m_of(k: float) -> np.ndarray:
                return matrix(w, h, Pose(pose.tx * k, pose.ty * k, pose.rot * k, pose.zoom * k), pivot, base)[0]
            k = self._keep_inside(m_of, box, w, h)
            if k < 1.0:
                pose = Pose(pose.tx * k, pose.ty * k, pose.rot * k, pose.zoom * k)
        m, s = matrix(w, h, pose, pivot, base)
        self.history.append((pose.tx, pose.ty))
        self.last_matrix = m
        q = cfg.quality
        if q == "auto":
            q = "cubic" if w * h <= 1280 * 720 and self.stats.ms < BUDGET_MS else "linear"
            if self.stats.ms > BUDGET_MS:
                q = "linear"
        flags = cv2.INTER_CUBIC if q == "cubic" else cv2.INTER_LINEAR
        if cv2.ocl.haveOpenCL() and cv2.ocl.useOpenCL():
            out = cv2.warpAffine(cv2.UMat(img), m, (w, h), flags=flags, borderMode=cv2.BORDER_REPLICATE).get()
            self.stats.backend = "OpenCL"
        else:
            out = cv2.warpAffine(img, m, (w, h), flags=flags, borderMode=cv2.BORDER_REPLICATE)
            self.stats.backend = "CPU"
        ms = (time.perf_counter() - t0) * 1000
        st = self.stats
        st.ms = ms if st.ms == 0 else 0.9 * st.ms + 0.1 * ms
        st.interpolation, st.scale, st.error = q, s, ""
        return out
