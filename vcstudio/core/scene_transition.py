"""Scene transitions (0.7.0): how the output goes from one scene to the next when the user switches scenes.

Each scene says how it is entered (profile body "scene": {"transition": kind, "transition_s": seconds}):
  cut    instantly (the behaviour before 0.7.0, and the default)
  fade   the last picture of the old scene dissolves into the new scene
  slide  the old picture moves out to the left while the new scene comes in from the right
  dip    the old picture fades to black, then the new scene fades in from black
The old scene is not rendered any more after the switch (its look is gone from the pipeline: one scene per frame,
vcstudio/core/scene.py), so the transition starts from the *last output frame of the old scene*, held still, and
blends into the live frames of the new scene. Only a real switch starts one: editing the active scene (which also
republishes its state) or renaming it does not. The transition runs on wall-clock time: when the new scene also
changes the input, no frames arrive while the capture re-opens, so a short transition can be over (or nearly) by the
time the new input's first frame is drawn.
Cost: one copy of the held frame at the switch (about 2.5 ms at 1080p), then one blend per frame while it runs
(fade / slide / dip about 3.3-3.7 ms per 1080p frame, measured on the 2-core development server).
"""
from __future__ import annotations

from typing import Callable

import cv2
import numpy as np

KINDS = ("cut", "fade", "slide", "dip")
MIN_S, MAX_S = 0.05, 5.0
DEFAULT_S = 0.5


def clean(kind, seconds) -> tuple[str, float]:
    """A valid (kind, seconds) pair (pure: tested)."""
    k = kind if kind in KINDS else "cut"
    try:
        s = min(MAX_S, max(MIN_S, float(seconds)))
    except (TypeError, ValueError):
        s = DEFAULT_S
    return k, s


def blend(old: np.ndarray, new: np.ndarray, kind: str, p: float) -> np.ndarray:
    """The picture `p` (0..1) of the way from `old` to `new` (pure: tested)."""
    p = min(1.0, max(0.0, p))
    if p >= 1.0 or kind == "cut" or old.shape != new.shape:
        return new
    if kind == "dip":
        if p < 0.5:
            return cv2.convertScaleAbs(old, alpha=1.0 - 2.0 * p)
        return cv2.convertScaleAbs(new, alpha=2.0 * p - 1.0)
    from vcstudio.core.multicam import transition_frame
    return transition_frame(old, new, "slide" if kind == "slide" else "fade", p)


class SceneTransition:
    """State of the running transition; driven once per output frame by the pipeline."""

    def __init__(self) -> None:
        self.entry: int | None = None        # SceneState.entry of the scene on screen
        self._old: np.ndarray | None = None
        self._t0 = 0.0
        self._kind = "cut"
        self._dur = DEFAULT_S
        self.count = 0                       # transitions started (status, tests)

    @property
    def running(self) -> bool:
        return self._old is not None

    def idle_for(self, entry: int) -> bool:
        """Nothing to do for this frame (the pipeline's fast path: no copy, no blend)."""
        return self._old is None and entry == self.entry

    def process(self, new: np.ndarray, entry: int, kind: str, dur: float, now: float,
                previous: Callable[[], np.ndarray | None]) -> np.ndarray:
        """The picture to send. `previous()` returns a copy of the last output of the old scene (called only at a
        switch)."""
        if entry != self.entry:
            first = self.entry is None
            self.entry = entry
            self._old = None
            if not first and kind != "cut":
                old = previous()
                if old is not None and old.shape == new.shape:
                    self._old, self._t0, self._kind, self._dur = old, now, kind, max(MIN_S, dur)
                    self.count += 1
        if self._old is None:
            return new
        p = (now - self._t0) / self._dur
        if p >= 1.0 or self._old.shape != new.shape:
            self._old = None
            return new
        return blend(self._old, new, self._kind, p)

    def reset(self) -> None:
        self._old = None
