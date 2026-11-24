"""Keyframe animation of drawn (manual, rectangle) regions (0.7.0): position, size, rotation and opacity over time.

ROI field "animation": {"enabled": bool, "duration_s": float, "loop": "loop" | "pingpong", "easing": "linear" |
"ease", "keys": [{"at": 0..1, "cx", "cy", "w", "h", "rotation", "opacity"}, ...]}. `at` is the share of the
duration; a key may leave out properties (they keep the region's drawn value). Between keys the values are
interpolated (ease = smoothstep, no jerk at the keys). Time is the frame's presentation time, so the animation runs
at the same speed on every output and pauses with nothing; "pingpong" plays forwards then backwards.

Presets build keys from the region's drawn geometry: pulse (opacity), slide in from the left, zoom (scale), spin
(rotation), sway (left-right drift). Auto / hybrid regions follow tracked objects instead and are not animated.
"""
from __future__ import annotations

import math

PROPS = ("cx", "cy", "w", "h", "rotation", "opacity")
LOOPS = ("loop", "pingpong")
EASINGS = ("linear", "ease")
PRESETS = ("pulse", "slide_in", "zoom", "spin", "sway")


def normalise(anim: dict | None) -> dict:
    """A clean animation dict (pure: tested): unknown keys dropped, values clamped, keys sorted by `at`."""
    a = anim if isinstance(anim, dict) else {}
    keys = []
    for k in a.get("keys") or []:
        if not isinstance(k, dict):
            continue
        try:
            at = min(1.0, max(0.0, float(k.get("at", 0.0))))
            item = {"at": at}
            for p in PROPS:
                if p in k:
                    item[p] = float(k[p])
            keys.append(item)
        except (TypeError, ValueError):
            continue
    keys.sort(key=lambda k: k["at"])
    try:
        dur = min(600.0, max(0.1, float(a.get("duration_s", 2.0))))
    except (TypeError, ValueError):
        dur = 2.0
    return {"enabled": bool(a.get("enabled", False)) and len(keys) >= 2, "duration_s": dur,
            "loop": a.get("loop") if a.get("loop") in LOOPS else "loop",
            "easing": a.get("easing") if a.get("easing") in EASINGS else "ease", "keys": keys}


def phase(t: float, duration: float, loop: str) -> float:
    """0..1 position in the animation at time t (pure)."""
    p = (t % duration) / duration
    if loop == "pingpong":
        q = (t % (2 * duration)) / duration
        p = q if q <= 1 else 2 - q
    return p


def sample(anim: dict, base: dict, t: float) -> dict:
    """The animated properties at time t; `base` = the region's drawn values (cx, cy, w, h, rotation, opacity)."""
    a = normalise(anim)
    keys = a["keys"]
    if not a["enabled"]:
        return {p: base[p] for p in PROPS}
    p = phase(t, a["duration_s"], a["loop"])
    if p <= keys[0]["at"]:
        k0 = k1 = keys[0]
        u = 0.0
    elif p >= keys[-1]["at"]:
        k0 = k1 = keys[-1]
        u = 0.0
    else:
        i = next(i for i in range(len(keys) - 1) if keys[i]["at"] <= p <= keys[i + 1]["at"])
        k0, k1 = keys[i], keys[i + 1]
        span = k1["at"] - k0["at"]
        u = (p - k0["at"]) / span if span > 0 else 0.0
    if a["easing"] == "ease":
        u = u * u * (3 - 2 * u)
    out = {}
    for prop in PROPS:
        v0, v1 = k0.get(prop, base[prop]), k1.get(prop, base[prop])
        out[prop] = v0 + (v1 - v0) * u
    out["w"], out["h"] = max(1e-3, out["w"]), max(1e-3, out["h"])
    out["opacity"] = min(1.0, max(0.0, out["opacity"]))
    return out


def preset(name: str, base: dict, duration_s: float = 2.0) -> dict:
    """An animation dict for a preset, built around the region's drawn geometry (pure: tested)."""
    cx, w, h, rot = base["cx"], base["w"], base["h"], base.get("rotation", 0.0)
    keys: list[dict]
    loop = "pingpong"
    if name == "pulse":
        keys = [{"at": 0.0, "opacity": 1.0}, {"at": 1.0, "opacity": 0.35}]
    elif name == "slide_in":
        keys = [{"at": 0.0, "cx": -w / 2}, {"at": 0.4, "cx": cx}, {"at": 1.0, "cx": cx}]
        loop = "loop"
    elif name == "zoom":
        keys = [{"at": 0.0, "w": w, "h": h}, {"at": 1.0, "w": w * 1.25, "h": h * 1.25}]
    elif name == "spin":
        keys = [{"at": 0.0, "rotation": rot}, {"at": 1.0, "rotation": rot + 360.0}]
        loop = "loop"
    elif name == "sway":
        d = min(0.15, w * 0.5)
        keys = [{"at": 0.0, "cx": cx - d, "rotation": rot - 4}, {"at": 1.0, "cx": cx + d, "rotation": rot + 4}]
    else:
        raise ValueError(f"unknown preset {name!r}")
    return {"enabled": True, "duration_s": duration_s, "loop": loop,
            "easing": "linear" if name == "spin" else "ease", "keys": keys}


def wrap_rotation(r: float) -> float:
    """Rotation folded into -180..180 (ROI validation range) (pure)."""
    return math.remainder(r, 360.0)
