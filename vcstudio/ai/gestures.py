"""Gesture control (roadmap P2.7, settings section "gestures"): a hand gesture held in front of the camera runs an
app action — the same actions as the hotkeys (vcstudio/ui/actions.py), e.g. open palm → privacy mode.

  GestureRecognizer   pure timing logic: a gesture fires once after it has been held `hold_s`; it fires again only
                      after the hand changed (another gesture or no hand for `release_s`) and `cooldown_s` passed —
                      a hand that stays up does not toggle privacy on and off
  GestureService      background thread: takes the latest INPUT frame (the camera picture, before any effect) at
                      `fps` (default 8), runs vcstudio/ai/hands.HandTracker, feeds the recognizer, calls the listeners
                      with (action id, gesture). Off by default; needs the hand models (Settings → Gestures → Download)
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from vcstudio.ai.hands import GESTURES

log = logging.getLogger(__name__)

DEFAULT_ACTIONS = {"open_palm": "privacy_toggle", "victory": "replay_save", "thumbs_up": "", "fist": "", "point": ""}


@dataclass
class GestureConfig:
    enabled: bool = False
    fps: float = 8.0
    hold_s: float = 0.6
    release_s: float = 0.3
    cooldown_s: float = 1.5
    actions: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_ACTIONS))

    @classmethod
    def from_settings(cls, settings) -> "GestureConfig":
        g = settings.get("gestures") or {}
        acts = dict(DEFAULT_ACTIONS)
        acts.update({k: str(v or "") for k, v in (g.get("actions") or {}).items() if k in GESTURES})
        return cls(enabled=bool(g.get("enabled", False)),
                   fps=float(min(15.0, max(1.0, g.get("fps", 8.0)))),
                   hold_s=float(min(3.0, max(0.2, g.get("hold_s", 0.6)))),
                   actions=acts)


class GestureRecognizer:
    def __init__(self, cfg: GestureConfig | None = None):
        self.cfg = cfg or GestureConfig()
        self.reset()

    def reset(self) -> None:
        self._cur = ""                 # gesture seen now
        self._since = 0.0              # when the current gesture started
        self._armed = True             # may fire (the hand changed since the last firing)
        self._fired = ""               # the gesture that fired last
        self._last_fire = -1e9
        self._other_since: float | None = None

    def update(self, gesture: str, t: float) -> str | None:
        """Feed the gesture seen at time t ("" = no hand / no known gesture). Returns the gesture that fires now."""
        if gesture != self._cur:
            self._cur, self._since = gesture, t
        if not self._armed:
            # re-arm once the hand has shown something else for release_s
            if gesture != self._fired:
                if self._other_since is None:
                    self._other_since = t
                if t - self._other_since >= self.cfg.release_s:
                    self._armed, self._other_since = True, None
            else:
                self._other_since = None
            return None
        if gesture and t - self._since >= self.cfg.hold_s and t - self._last_fire >= self.cfg.cooldown_s:
            self._armed, self._last_fire, self._fired, self._other_since = False, t, gesture, None
            return gesture
        return None


@dataclass
class GestureStats:
    state: str = "off"            # off | starting | running | no_models | error
    fps: float = 0.0
    ms: float = 0.0
    hands: int = 0
    gesture: str = ""
    last_action: str = ""
    last_action_at: float = 0.0
    error: str = ""
    backend: str = ""


class GestureService:
    def __init__(self, capture, settings, log_event: Callable[[str, str], None] | None = None,
                 cache_dir=None, tracker_factory=None):
        self.capture, self.settings = capture, settings
        self._log = log_event or (lambda lv, m: None)
        self.cache_dir = cache_dir
        self._factory = tracker_factory
        self.listeners: list[Callable[[str, str], None]] = []
        self.stats = GestureStats()
        self.cfg = GestureConfig.from_settings(settings)
        self.recognizer = GestureRecognizer(self.cfg)
        self.last_hands: list = []
        self._t: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def running(self) -> bool:
        return self._t is not None and self._t.is_alive()

    def apply_settings(self) -> None:
        self.cfg = GestureConfig.from_settings(self.settings)
        self.recognizer.cfg = self.cfg
        if self.cfg.enabled and not self.running:
            self.start()
        elif not self.cfg.enabled and self.running:
            self.stop()

    def start(self) -> None:
        if self.running:
            return
        self._stop = threading.Event()
        self.recognizer.reset()
        self.stats = GestureStats(state="starting")
        self._t = threading.Thread(target=self._run, args=(self._stop,), name="gestures", daemon=True)
        self._t.start()

    def stop(self) -> None:
        self._stop.set()
        if self._t is not None:
            self._t.join(5)
        self._t = None
        self.stats.state = "off"
        self.last_hands = []

    def _make_tracker(self):
        if self._factory is not None:
            return self._factory()
        from vcstudio.ai import hands
        if not hands.available():
            return None
        return hands.HandTracker("all", self.cache_dir)

    def _run(self, stop: threading.Event) -> None:
        try:
            tracker = self._make_tracker()
        except Exception as e:
            self.stats.state, self.stats.error = "error", f"{type(e).__name__}: {e}"
            self._log("ERROR", f"gestures: hand models did not load: {self.stats.error}")
            return
        if tracker is None:
            self.stats.state = "no_models"
            return
        self.stats.state, self.stats.backend = "running", getattr(tracker, "backend", "")
        last_ver = None
        n, t_win = 0, time.monotonic()
        while not stop.is_set():
            t0 = time.monotonic()
            frame, ver = self.capture.slot.peek()
            if frame is not None and ver != last_ver:
                last_ver = ver
                try:
                    with frame.bgra() as a:
                        bgr = np.ascontiguousarray(a[..., :3])
                    hs = tracker.hands(bgr)
                    self.last_hands = hs
                    best = max(hs, key=lambda h: h.confidence, default=None)
                    g = best.gesture if best is not None else ""
                    self.stats.hands, self.stats.gesture = len(hs), g
                    fired = self.recognizer.update(g, time.monotonic())
                    if fired:
                        self._fire(fired)
                    self.stats.ms = (time.monotonic() - t0) * 1000
                    n += 1
                except Exception as e:
                    self.stats.error = f"{type(e).__name__}: {e}"
                    log.exception("gesture frame failed")
            if time.monotonic() - t_win >= 2.0:
                self.stats.fps, n, t_win = n / (time.monotonic() - t_win), 0, time.monotonic()
            stop.wait(max(0.0, 1.0 / self.cfg.fps - (time.monotonic() - t0)))

    def _fire(self, gesture: str) -> None:
        aid = self.cfg.actions.get(gesture, "")
        if not aid:
            return
        self.stats.last_action, self.stats.last_action_at = f"{gesture} → {aid}", time.monotonic()
        self._log("INFO", f"gesture {gesture} → {aid}")
        for cb in tuple(self.listeners):
            try:
                cb(aid, gesture)
            except Exception:
                log.exception("gesture listener failed")
