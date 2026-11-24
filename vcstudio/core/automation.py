"""Smart scene automation (0.6.0 Phase 7): rules "when <trigger> [and <condition>] do <action>".

Builds on what exists: scenes switch through SceneManager.switch (atomic SceneState publishing), everything else
through the app's action bus (the same action ids as hotkeys, gestures, tray and the remote API), multi-camera and
colour presets through their services. No second scene system, no second event system.

Settings section "automation": {"enabled": bool, "rules": [rule, ...]}. A rule:
  {"id", "name", "enabled", "priority" (higher first), "cooldown_s", "debounce_s",
   "trigger":   {"type": T, ...},   "condition": {"scene": name | "", "min_people": n | None, "max_people": n | None},
   "action":    {"type": A, ...}}
Triggers T (state triggers fire on the change, after the new state held `debounce_s`; event triggers fire at once):
  person_detected · person_lost · people_count {"op": ">=" | "<=" | "==", "n"} · object_detected {"label"} ·
  face_count_changed · gesture {"gesture"} · camera_disconnected {"source": "main" | extra id} ·
  recording_started · recording_stopped · timer {"every_s"} · hotkey {"slot": 1..4} · manual ·
  looking_at_camera · looking_away (0.7.0: the largest face's gaze, AI Vision with gaze on; no confident gaze =
  neither, so a blink or a lost face does not fire "looking away")
Condition groups (0.7.0): besides the plain keys above, "condition" may hold {"match": "all" | "any",
"terms": [term, ...]} — all terms must hold (AND) or at least one (OR); every term may carry "not": true. Terms:
  people {"op", "n"} · scene {"name"} · object {"label"} · recording · gaze (largest face looks at the camera) ·
  camera {"source"} (connected) · time {"from": "HH:MM", "to": "HH:MM"} (local time; may pass midnight)
parse_condition() reads the rule builder's text form, e.g. "people>=2 and not recording", "gaze or time 09:00-18:00".
Actions A:
  scene {"name"} · action {"id": action id} · privacy {"level"} · record {"do": "start" | "stop"} · replay ·
  camera {"id"} · layout {"layout"} · color_preset {"name"} · effects {"on": bool}

Safety: per-rule cooldown; debounce on state triggers (hysteresis: a flickering detection does not fire); events
de-duplicated (same event within DEDUP_S); a bounded event queue (oldest dropped and counted); per event only the
highest-priority scene switch runs; loop protection: more than MAX_SCENE_SWITCHES automatic scene switches within
SWITCH_WINDOW_S pause scene actions for PAUSE_S and log it; a rule whose action raises is counted and keeps running
(its cooldown still applies). Every decision goes into a bounded history shown in the UI.
"""
from __future__ import annotations

import collections
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger(__name__)

TRIGGERS = ("person_detected", "person_lost", "people_count", "object_detected", "face_count_changed", "gesture",
            "camera_disconnected", "recording_started", "recording_stopped", "timer", "hotkey", "manual",
            "looking_at_camera", "looking_away")
ACTIONS = ("scene", "action", "privacy", "record", "replay", "camera", "layout", "color_preset", "effects")
STATE_TRIGGERS = ("person_detected", "person_lost", "people_count", "object_detected", "face_count_changed",
                  "camera_disconnected", "recording_started", "recording_stopped", "looking_at_camera",
                  "looking_away")
QUEUE_MAX = 256
DEDUP_S = 0.2
MAX_SCENE_SWITCHES = 4
SWITCH_WINDOW_S = 20.0
PAUSE_S = 30.0
HISTORY_MAX = 200
TICK_S = 0.25


@dataclass
class Rule:
    id: str
    name: str = ""
    enabled: bool = True
    priority: int = 0
    cooldown_s: float = 5.0
    debounce_s: float = 1.0
    trigger: dict = field(default_factory=dict)
    condition: dict = field(default_factory=dict)
    action: dict = field(default_factory=dict)

    @staticmethod
    def from_dict(d: dict, i: int = 0) -> Rule | None:
        if not isinstance(d, dict):
            return None
        trig, act = d.get("trigger") or {}, d.get("action") or {}
        if trig.get("type") not in TRIGGERS or act.get("type") not in ACTIONS:
            return None

        def num(v, lo, hi, default):
            try:
                return min(hi, max(lo, float(v)))
            except (TypeError, ValueError):
                return default
        return Rule(str(d.get("id") or f"rule{i + 1}"), str(d.get("name") or ""), bool(d.get("enabled", True)),
                    int(num(d.get("priority"), -100, 100, 0)), num(d.get("cooldown_s"), 0, 3600, 5.0),
                    num(d.get("debounce_s"), 0, 60, 1.0), dict(trig), dict(d.get("condition") or {}), dict(act))


@dataclass
class Snapshot:
    """What the state triggers look at, read once per tick."""
    people: int = 0
    faces: int = 0
    labels: frozenset = frozenset()
    recording: bool = False
    scene: str = ""
    sources: dict = field(default_factory=dict)       # "main" / extra id -> connected (bool)
    gaze: bool | None = None                          # largest face looks at the camera; None: no confident gaze
    minute: int = -1                                  # local time of day in minutes (time terms), -1: unknown


TERMS = ("people", "scene", "object", "recording", "gaze", "camera", "time")


def _hhmm(t: str) -> int:
    h, m = str(t).strip().split(":")
    v = int(h) * 60 + int(m)
    if not 0 <= v < 24 * 60:
        raise ValueError(t)
    return v


def term_ok(t: dict, s: Snapshot) -> bool:
    """One condition term against a snapshot (pure: tested). An unknown or broken term is false."""
    k = t.get("type")
    try:
        if k == "people":
            n, op = int(t.get("n", 1)), t.get("op", ">=")
            v = {">=": s.people >= n, "<=": s.people <= n, "==": s.people == n}.get(op, s.people >= n)
        elif k == "scene":
            v = s.scene == t.get("name")
        elif k == "object":
            v = str(t.get("label", "")) in s.labels
        elif k == "recording":
            v = bool(s.recording)
        elif k == "gaze":
            v = s.gaze is True
        elif k == "camera":
            v = bool(s.sources.get(t.get("source") or "main", False))
        elif k == "time":
            if s.minute < 0:
                return False
            a, b = _hhmm(t["from"]), _hhmm(t["to"])
            v = a <= s.minute < b if a <= b else (s.minute >= a or s.minute < b)
        else:
            return False
    except (KeyError, TypeError, ValueError):
        return False
    return (not v) if t.get("not") else v


def group_ok(c: dict, s: Snapshot) -> bool:
    terms = [t for t in (c.get("terms") or []) if isinstance(t, dict)]
    if not terms:
        return True
    res = (term_ok(t, s) for t in terms)
    return any(res) if c.get("match") == "any" else all(res)


def parse_condition(text: str) -> dict:
    """Text of the rule builder -> {"match", "terms"}; raises ValueError with a readable reason (pure: tested).
    Words: people>=2 · people<=1 · people==3 · scene:Name · object:laptop · recording · gaze · camera:cam1 ·
    time 09:00-18:00, each optionally after "not", joined by "and" or by "or" (not both)."""
    t = " ".join(str(text).split())
    if not t:
        return {}
    low = f" {t.lower()} "
    if " and " in low and " or " in low:
        raise ValueError("use either 'and' or 'or' in one condition")
    match = "any" if " or " in low else "all"
    import re
    parts = re.split(r"\s+(?:and|or)\s+", t, flags=re.I)
    terms = []
    for p in parts:
        neg = False
        if p.lower().startswith("not "):
            neg, p = True, p[4:].strip()
        low_p = p.lower()
        m = re.fullmatch(r"people\s*(>=|<=|==)\s*(\d+)", low_p)
        if m:
            term: dict = {"type": "people", "op": m.group(1), "n": int(m.group(2))}
        elif low_p.startswith("scene:"):
            term = {"type": "scene", "name": p[6:].strip()}
        elif low_p.startswith("object:"):
            term = {"type": "object", "label": p[7:].strip()}
        elif low_p.startswith("camera:"):
            term = {"type": "camera", "source": p[7:].strip()}
        elif low_p in ("recording", "gaze"):
            term = {"type": low_p}
        elif (m := re.fullmatch(r"time\s+(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})", low_p)):
            _hhmm(m.group(1))
            _hhmm(m.group(2))
            term = {"type": "time", "from": m.group(1), "to": m.group(2)}
        else:
            raise ValueError(f"not understood: {p!r}")
        if neg:
            term["not"] = True
        terms.append(term)
    return {"match": match, "terms": terms}


def describe_condition(c: dict) -> str:
    """The text form of a condition group (parse_condition's inverse)."""
    out = []
    for t in c.get("terms") or []:
        k = t.get("type")
        w = {"people": f"people{t.get('op', '>=')}{t.get('n', 1)}", "scene": f"scene:{t.get('name', '')}",
             "object": f"object:{t.get('label', '')}", "camera": f"camera:{t.get('source', '')}",
             "time": f"time {t.get('from', '')}-{t.get('to', '')}"}.get(k, str(k))
        out.append(("not " if t.get("not") else "") + w)
    return (" or " if c.get("match") == "any" else " and ").join(out)


@dataclass
class HistoryItem:
    t: float
    rule: str
    trigger: str
    action: str
    result: str


class AutomationEngine:
    def __init__(self, snapshot: Callable[[], Snapshot], execute: Callable[[dict], str], log_event=None):
        self.snapshot = snapshot                      # reads the app state (people, faces, recording, ...)
        self.execute = execute                        # runs one action, returns a short result text
        self._log = log_event or (lambda lv, m: None)
        self.enabled = False
        self.rules: list[Rule] = []
        self.history: collections.deque[HistoryItem] = collections.deque(maxlen=HISTORY_MAX)
        self.dropped = 0
        self.errors = 0
        self._q: collections.deque[tuple[str, dict, float]] = collections.deque()
        self._qlock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._t: threading.Thread | None = None
        self._last_fire: dict[str, float] = {}
        self._pending: dict[str, tuple[Any, float]] = {}   # rule id -> (candidate state, since)
        self._state: dict[str, Any] = {}                  # rule id -> last accepted state
        self._last_event: dict[tuple, float] = {}
        self._switches: collections.deque[float] = collections.deque()
        self._paused_until = 0.0
        self._timers: dict[str, float] = {}

    # ------------------------------------------------------------------ config / lifecycle
    def configure(self, cfg: dict) -> None:
        cfg = cfg or {}
        rules = [r for r in (Rule.from_dict(d, i) for i, d in enumerate(cfg.get("rules") or [])) if r]
        self.rules = sorted(rules, key=lambda r: -r.priority)
        self.enabled = bool(cfg.get("enabled", False))
        keep = {r.id for r in self.rules}
        for d in (self._last_fire, self._pending, self._state, self._timers):
            for k in [k for k in d if k not in keep]:
                d.pop(k)
        if self.enabled and not self.running:
            self.start()
        elif not self.enabled and self.running:
            self.stop()

    @property
    def running(self) -> bool:
        return self._t is not None and self._t.is_alive()

    def start(self) -> None:
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, args=(self._stop,), name="automation", daemon=True)
        self._t.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._t is not None:
            self._t.join(2)
        self._t = None

    # ------------------------------------------------------------------ events
    def emit(self, kind: str, **data) -> None:
        """An event (gesture, hotkey slot, manual run) from any thread; bounded, never blocks."""
        with self._qlock:
            if len(self._q) >= QUEUE_MAX:
                self._q.popleft()
                self.dropped += 1
            self._q.append((kind, data, time.monotonic()))
        self._wake.set()

    def _run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            self._wake.wait(TICK_S)
            self._wake.clear()
            try:
                self.step()
            except Exception:
                log.exception("automation step failed")

    def step(self, now: float | None = None) -> None:
        """One round: queued events, then state triggers and timers (also called directly by tests)."""
        now = time.monotonic() if now is None else now
        with self._qlock:
            events = list(self._q)
            self._q.clear()
        try:
            snap = self.snapshot()
        except Exception:
            log.exception("automation snapshot failed")
            snap = Snapshot()
        for kind, data, _t in events:
            key = (kind, tuple(sorted(data.items())))
            if now - self._last_event.get(key, -1e9) < DEDUP_S:
                continue
            self._last_event[key] = now
            self._fire_matching(lambda r, k=kind, d=data: self._event_matches(r, k, d), snap, now, kind)
        self._fire_matching(lambda r: self._state_matches(r, snap, now), snap, now, "state")
        if len(self._last_event) > 512:
            self._last_event = {k: v for k, v in self._last_event.items() if now - v < 5}

    # ------------------------------------------------------------------ matching
    @staticmethod
    def _event_matches(r: Rule, kind: str, data: dict) -> bool:
        tt = r.trigger.get("type")
        if kind == "gesture":
            return tt == "gesture" and r.trigger.get("gesture") in (None, "", data.get("gesture"))
        if kind == "hotkey":
            return tt == "hotkey" and int(r.trigger.get("slot", 1) or 1) == int(data.get("slot", 0))
        if kind == "manual":                                 # "Run now": that rule whatever its trigger
            return data.get("rule") == r.id or (tt == "manual" and data.get("rule") is None)
        return False

    def _value(self, r: Rule, s: Snapshot) -> Any:
        tt = r.trigger.get("type")
        if tt in ("person_detected", "person_lost"):
            return s.people > 0
        if tt == "people_count":
            n, op = int(r.trigger.get("n", 1) or 0), r.trigger.get("op", ">=")
            return {">=": s.people >= n, "<=": s.people <= n, "==": s.people == n}.get(op, s.people >= n)
        if tt == "object_detected":
            return str(r.trigger.get("label", "")) in s.labels
        if tt == "face_count_changed":
            return s.faces
        if tt == "camera_disconnected":
            return not s.sources.get(r.trigger.get("source") or "main", True)
        if tt in ("recording_started", "recording_stopped"):
            return s.recording
        if tt in ("looking_at_camera", "looking_away"):
            return s.gaze
        return None

    def _state_matches(self, r: Rule, s: Snapshot, now: float) -> bool:
        tt = r.trigger.get("type")
        if tt == "timer":
            every = max(1.0, float(r.trigger.get("every_s", 60) or 60))
            nxt = self._timers.setdefault(r.id, now + every)
            if now >= nxt:
                self._timers[r.id] = now + every
                return True
            return False
        if tt not in STATE_TRIGGERS:
            return False
        v = self._value(r, s)
        if r.id not in self._state:                      # first look: remember, never fire on start-up
            self._state[r.id] = v
            return False
        if v == self._state[r.id]:
            self._pending.pop(r.id, None)
            return False
        cand = self._pending.get(r.id)
        if cand is None or cand[0] != v:
            self._pending[r.id] = (v, now)
            cand = (v, now)
        if now - cand[1] < r.debounce_s:                  # hysteresis: the new state must hold
            return False
        prev = self._state[r.id]
        self._state[r.id] = v
        self._pending.pop(r.id, None)
        return {"person_detected": v is True, "person_lost": v is False, "people_count": v is True,
                "object_detected": v is True, "face_count_changed": v != prev, "camera_disconnected": v is True,
                "recording_started": v is True, "recording_stopped": v is False,
                "looking_at_camera": v is True, "looking_away": v is False}[tt]

    def _condition_ok(self, r: Rule, s: Snapshot) -> bool:
        c = r.condition
        if c.get("scene") and c["scene"] != s.scene:
            return False
        if c.get("min_people") is not None and s.people < int(c["min_people"]):
            return False
        if c.get("max_people") is not None and s.people > int(c["max_people"]):
            return False
        return group_ok(c, s)

    # ------------------------------------------------------------------ firing
    def _fire_matching(self, matches, snap: Snapshot, now: float, source: str) -> None:
        if not self.enabled:
            return
        scene_done = False
        for r in self.rules:                              # sorted by priority, highest first
            if not r.enabled or not matches(r):
                continue
            if not self._condition_ok(r, snap):
                self._record(now, r, "condition not met")
                continue
            if now - self._last_fire.get(r.id, -1e9) < r.cooldown_s:
                self._record(now, r, "cooldown")
                continue
            if r.action.get("type") == "scene":
                if scene_done:
                    self._record(now, r, "skipped: a higher-priority scene switch ran")
                    continue
                if now < self._paused_until:
                    self._record(now, r, "skipped: loop protection")
                    continue
                if r.action.get("name") == snap.scene:
                    self._record(now, r, "already in this scene")
                    self._last_fire[r.id] = now
                    continue
                while self._switches and now - self._switches[0] > SWITCH_WINDOW_S:
                    self._switches.popleft()
                if len(self._switches) >= MAX_SCENE_SWITCHES:
                    self._paused_until = now + PAUSE_S
                    msg = (f"automation: {MAX_SCENE_SWITCHES} scene switches in {SWITCH_WINDOW_S:.0f} s; scene "
                           f"actions paused for {PAUSE_S:.0f} s (loop protection)")
                    log.warning(msg)
                    self._log("WARNING", msg)
                    self._record(now, r, "loop protection: paused")
                    continue
                self._switches.append(now)
                scene_done = True
            self._last_fire[r.id] = now
            try:
                res = self.execute(r.action) or "done"
            except Exception as e:                        # noqa: BLE001 - one failing action must not stop the rest
                self.errors += 1
                res = f"failed: {type(e).__name__}: {e}"
                log.exception("automation action failed (%s)", r.name or r.id)
            self._record(now, r, res)

    def _record(self, now: float, r: Rule, result: str) -> None:
        a = r.action
        act = a.get("type", "") + (f" {a.get('name') or a.get('id') or a.get('level') or a.get('do') or a.get('layout') or ''}"
                                   if len(a) > 1 else "")
        self.history.append(HistoryItem(now, r.name or r.id, r.trigger.get("type", ""), act.strip(), result))

    def run_rule(self, rule_id: str) -> None:
        """The UI's "Run now" (trigger type manual, or testing any rule)."""
        self.emit("manual", rule=rule_id)
