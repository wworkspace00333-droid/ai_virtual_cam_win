"""Resource scheduler: one governor for every optional heavy module, so the camera keeps its frame rate.

The output thread is paced by the input; when the PC is overloaded the output rate falls below the input rate. The
scheduler watches that one signal and, instead of each module guessing on its own, slows the modules down in
priority order:

  overloaded (output < low × input for `down_after` s): the LOWEST-priority active module that can still slow down
                                                         gets scale × step_down (not below its min_scale)
  headroom   (output ≥ high × input for `up_after` s):   the HIGHEST-priority throttled module gets scale × step_up

Modules (registered by the app):
  background   person segmentation for background blur / replacement   priority 1 (first to slow: masks are smoothed)
  detector     object / face / any-object detection                     priority 2 (regions and face hiding follow it)
Only active modules take part (background on, AI enabled); an inactive module is reset to full rate. The detector's
own "on demand" standby (no region needs it, 2 fps) is applied by the AI engine on top of this.

A module exposes: `active()`, `set_scale(scale, reason)`, and for the status panel `rate_fps()`, `cost_ms()`,
`state()`. The scheduler never blocks the video: it runs in its own thread every `period` seconds and only changes
numbers the modules read.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class Module:
    name: str
    priority: int                                   # higher = more important = slowed last
    min_scale: float
    active: Callable[[], bool]
    apply: Callable[[float, str], None]             # (scale, reason)
    rate_fps: Callable[[], float] = lambda: 0.0
    cost_ms: Callable[[], float] = lambda: 0.0
    state: Callable[[], str] = lambda: ""
    scale: float = 1.0
    reason: str = ""


@dataclass
class ModuleStatus:
    name: str
    priority: int
    active: bool
    scale: float
    rate_fps: float
    cost_ms: float
    state: str
    reason: str


@dataclass
class SchedulerStatus:
    load: str = "idle"                              # idle | ok | overloaded | recovering
    output_fps: float = 0.0
    input_fps: float = 0.0
    steps_down: int = 0
    steps_up: int = 0
    modules: list[ModuleStatus] = field(default_factory=list)
    last_action: str = ""


class ResourceScheduler:
    def __init__(self, probe: Callable[[], tuple[float, float]], *, low: float = 0.90, high: float = 0.97,
                 down_after: float = 2.0, up_after: float = 5.0, step_down: float = 0.75, step_up: float = 1.15,
                 period: float = 0.5, event_sink: Callable[[str, str], None] | None = None):
        self.probe = probe                          # () -> (output fps, input fps); (0, 0) when not running
        self.low, self.high = low, high
        self.down_after, self.up_after = down_after, up_after
        self.step_down, self.step_up = step_down, step_up
        self.period = period
        self.modules: list[Module] = []
        self.status = SchedulerStatus()
        self._sink = event_sink or (lambda level, msg: None)
        self._over_since: float | None = None
        self._ok_since: float | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._t: threading.Thread | None = None

    def add(self, module: Module) -> Module:
        with self._lock:
            self.modules.append(module)
            self.modules.sort(key=lambda m: m.priority)
        module.apply(module.scale, "")
        return module

    # ---------------- control loop
    def step(self, now: float | None = None) -> None:
        """One evaluation (the thread calls this every `period`; tests call it directly with their own clock)."""
        now = time.monotonic() if now is None else now
        try:
            out_fps, in_fps = self.probe()
        except Exception:                           # a probe must never stop the scheduler
            out_fps, in_fps = 0.0, 0.0
        with self._lock:
            mods = list(self.modules)
        active = [m for m in mods if self._is_active(m)]
        for m in mods:                              # an idle module starts at full rate next time it is used
            if m not in active and m.scale != 1.0:
                self._set(m, 1.0, "")
        st = self.status
        st.output_fps, st.input_fps = out_fps, in_fps
        if in_fps < 5 or out_fps <= 0:              # no video: nothing to judge
            self._over_since = self._ok_since = None
            st.load = "idle"
        elif out_fps < self.low * in_fps:
            self._ok_since = None
            self._over_since = self._over_since if self._over_since is not None else now
            st.load = "overloaded"
            if now - self._over_since >= self.down_after:
                self._over_since = now
                victim = next((m for m in active if m.scale > m.min_scale + 1e-9), None)   # lowest priority first
                if victim is not None:
                    new = max(victim.min_scale, victim.scale * self.step_down)
                    why = f"output {out_fps:.0f} of {in_fps:.0f} fps: {victim.name} slowed to {new:.0%}"
                    self._set(victim, new, why)
                    st.steps_down += 1
                    st.last_action = why
                    self._sink("WARNING", why)
        elif out_fps >= self.high * in_fps:
            self._over_since = None
            self._ok_since = self._ok_since if self._ok_since is not None else now
            throttled = [m for m in reversed(active) if m.scale < 1.0]                     # highest priority first
            st.load = "recovering" if throttled else "ok"
            if throttled and now - self._ok_since >= self.up_after:
                self._ok_since = now
                m = throttled[0]
                new = min(1.0, m.scale * self.step_up)
                why = "" if new >= 1.0 else f"recovering: {m.name} at {new:.0%}"
                self._set(m, new, why)
                st.steps_up += 1
                st.last_action = why or f"{m.name} back to full rate"
        else:
            self._over_since = self._ok_since = None
            st.load = "ok"
        st.modules = [self._status_of(m) for m in mods]

    def _is_active(self, m: Module) -> bool:
        try:
            return bool(m.active())
        except Exception:
            return False

    def _set(self, m: Module, scale: float, reason: str) -> None:
        m.scale, m.reason = scale, reason
        try:
            m.apply(scale, reason)
        except Exception:
            log.exception("scheduler: %s could not apply scale %.2f", m.name, scale)

    def _status_of(self, m: Module) -> ModuleStatus:
        def safe(fn, default):
            try:
                return fn()
            except Exception:
                return default
        return ModuleStatus(m.name, m.priority, self._is_active(m), m.scale, float(safe(m.rate_fps, 0.0)),
                            float(safe(m.cost_ms, 0.0)), str(safe(m.state, "")), m.reason)

    def reset(self) -> None:
        for m in list(self.modules):
            self._set(m, 1.0, "")
        self._over_since = self._ok_since = None

    # ---------------- thread
    def start(self) -> None:
        if self._t is not None and self._t.is_alive():
            return
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, args=(self._stop,), name="scheduler", daemon=True)
        self._t.start()

    def _run(self, stop: threading.Event) -> None:
        while not stop.wait(self.period):
            try:
                self.step()
            except Exception:
                log.exception("scheduler step failed")

    def stop(self) -> None:
        self._stop.set()
        if self._t is not None:
            self._t.join(2)
        self._t = None
        self.reset()
