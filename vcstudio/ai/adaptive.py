"""Adaptive detector rate: under overload the AI slows down, not the camera.

The output thread is paced by the input (one output frame per input frame). When the machine is overloaded, the
output rate falls below the input rate. AI inference is the biggest optional load and the tracker predicts boxes
between detections, so the detector rate is lowered first: the camera keeps its frame rate and only the boxes update
less often.

Control loop, evaluated at most every `period` seconds:
  overloaded (output < low * input for `down_after` s)  -> scale *= step_down   (not below `min_scale`)
  headroom   (output >= high * input for `up_after` s)  -> scale *= step_up     (not above 1.0)
The detector target becomes mode_rate * scale (never below `min_fps`). Slow to go up, quick to go down, so it does
not oscillate. Disabled until the pipeline produces frames.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AdaptiveAIRate:
    low: float = 0.90           # output below 90 % of the input rate = overloaded
    high: float = 0.97          # output at 97 % or more = headroom
    down_after: float = 2.0     # seconds of overload before stepping down
    up_after: float = 5.0       # seconds of headroom before stepping up
    step_down: float = 0.75
    step_up: float = 1.15
    min_scale: float = 0.25
    min_fps: float = 2.0
    period: float = 0.5
    enabled: bool = True

    scale: float = 1.0
    reason: str = ""
    _last: float = -1e9
    _over_since: float | None = None
    _ok_since: float | None = None

    def reset(self) -> None:
        self.scale, self.reason = 1.0, ""
        self._over_since = self._ok_since = None

    def update(self, now: float, output_fps: float, input_fps: float) -> float:
        """Feed the measured rates; returns the current scale (1.0 = the mode's own detector rate)."""
        if not self.enabled:
            self.scale = 1.0
            return self.scale
        if now - self._last < self.period:
            return self.scale
        self._last = now
        if input_fps < 5 or output_fps <= 0:             # no video yet / input stalled: nothing to judge
            self._over_since = self._ok_since = None
            return self.scale
        if output_fps < self.low * input_fps:
            self._ok_since = None
            self._over_since = self._over_since if self._over_since is not None else now
            if now - self._over_since >= self.down_after and self.scale > self.min_scale:
                self.scale = max(self.min_scale, self.scale * self.step_down)
                self.reason = f"output {output_fps:.0f} of {input_fps:.0f} fps: detector slowed to {self.scale:.0%}"
                self._over_since = now                   # wait again before the next step
        elif output_fps >= self.high * input_fps:
            self._over_since = None
            self._ok_since = self._ok_since if self._ok_since is not None else now
            if now - self._ok_since >= self.up_after and self.scale < 1.0:
                self.scale = min(1.0, self.scale * self.step_up)
                self.reason = "" if self.scale >= 1.0 else f"recovering: detector at {self.scale:.0%}"
                self._ok_since = now
        else:
            self._over_since = self._ok_since = None
        return self.scale

    def apply(self, mode_fps: float | None, achieved_fps: float) -> float | None:
        """The detector target for a mode rate (None = as fast as possible, Maximum Quality)."""
        if self.scale >= 1.0:
            return mode_fps
        base = mode_fps if mode_fps else max(achieved_fps, self.min_fps / self.scale)
        return max(self.min_fps, base * self.scale)
