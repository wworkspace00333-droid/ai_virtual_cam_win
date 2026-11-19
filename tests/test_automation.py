"""0.6.0 Phase 7: automation rules — triggers, debounce, cooldown, priority, loop protection, history, the app."""
from __future__ import annotations

import time

from vcstudio.core import automation as au


class World:
    def __init__(self):
        self.snap = au.Snapshot(scene="A")
        self.done: list[dict] = []

    def snapshot(self):
        return self.snap

    def execute(self, act):
        self.done.append(act)
        if act.get("type") == "scene":
            self.snap = au.Snapshot(**{**self.snap.__dict__, "scene": act["name"]})
        if act.get("type") == "replay" and act.get("boom"):
            raise RuntimeError("boom")
        return "ok"


def engine(rules, world=None):
    w = world or World()
    e = au.AutomationEngine(w.snapshot, w.execute)
    e.rules = sorted([au.Rule.from_dict(r, i) for i, r in enumerate(rules)], key=lambda r: -r.priority)
    e.enabled = True
    return e, w


def people(w, n):
    w.snap = au.Snapshot(**{**w.snap.__dict__, "people": n})


def test_rules_are_validated():
    assert au.Rule.from_dict({"trigger": {"type": "nope"}, "action": {"type": "scene"}}) is None
    assert au.Rule.from_dict({"trigger": {"type": "timer"}, "action": {"type": "x"}}) is None
    r = au.Rule.from_dict({"trigger": {"type": "timer"}, "action": {"type": "replay"}, "cooldown_s": -5,
                           "priority": 500}, 3)
    assert r.id == "rule4" and r.cooldown_s == 0 and r.priority == 100


def test_person_lost_needs_debounce_then_fires_once():
    e, w = engine([{"id": "away", "trigger": {"type": "person_lost"}, "debounce_s": 2.0, "cooldown_s": 0,
                    "action": {"type": "scene", "name": "Away"}}])
    people(w, 1)
    e.step(0.0)                                   # first look: remembered, never fires on start-up
    people(w, 0)
    e.step(1.0)
    people(w, 1)
    e.step(1.5)                                   # back before 2 s: the timer resets (hysteresis)
    people(w, 0)
    e.step(2.0)
    e.step(3.9)
    assert w.done == []
    e.step(4.1)
    assert w.done == [{"type": "scene", "name": "Away"}]
    e.step(10.0)
    assert len(w.done) == 1                       # a state trigger fires on the change, not while it lasts


def test_cooldown_condition_and_people_count():
    e, w = engine([{"id": "many", "trigger": {"type": "people_count", "op": ">=", "n": 2}, "debounce_s": 0,
                    "cooldown_s": 30, "condition": {"scene": "A"}, "action": {"type": "privacy", "level": "faces"}}])
    e.step(0)
    people(w, 3)
    e.step(1)
    assert w.done == [{"type": "privacy", "level": "faces"}]
    people(w, 0)
    e.step(2)
    people(w, 2)
    e.step(3)
    assert len(w.done) == 1 and e.history[-1].result == "cooldown"
    w.snap = au.Snapshot(scene="B", people=0)
    e.step(40)
    w.snap = au.Snapshot(scene="B", people=5)
    e.step(41)
    assert len(w.done) == 1 and e.history[-1].result == "condition not met"


def test_events_gestures_hotkeys_dedup_and_priority():
    e, w = engine([
        {"id": "lo", "priority": 1, "cooldown_s": 0, "trigger": {"type": "gesture", "gesture": "victory"},
         "action": {"type": "scene", "name": "Low"}},
        {"id": "hi", "priority": 9, "cooldown_s": 0, "trigger": {"type": "gesture", "gesture": "victory"},
         "action": {"type": "scene", "name": "High"}},
        {"id": "rep", "priority": 0, "cooldown_s": 0, "trigger": {"type": "gesture", "gesture": "victory"},
         "action": {"type": "replay"}},
        {"id": "hk", "cooldown_s": 0, "trigger": {"type": "hotkey", "slot": 2}, "action": {"type": "effects",
                                                                                            "on": False}}])
    e.emit("gesture", gesture="victory")
    e.emit("gesture", gesture="victory")          # duplicate within DEDUP_S: ignored
    e.step(100.0)
    assert w.done == [{"type": "scene", "name": "High"}, {"type": "replay"}]   # one scene switch per event
    assert any("higher-priority" in h.result for h in e.history)
    e.emit("hotkey", slot=1)
    e.emit("hotkey", slot=2)
    e.step(101.0)
    assert w.done[-1] == {"type": "effects", "on": False} and len(w.done) == 3


def test_loop_protection_stops_scene_ping_pong():
    e, w = engine([
        {"id": "toB", "cooldown_s": 0, "trigger": {"type": "timer", "every_s": 1}, "action": {"type": "scene",
                                                                                             "name": "B"}},
        {"id": "toA", "cooldown_s": 0, "trigger": {"type": "timer", "every_s": 1}, "action": {"type": "scene",
                                                                                             "name": "A"}}])
    t = 0.0
    for _ in range(int(au.SWITCH_WINDOW_S) + 5):
        e.step(t)
        t += 1.0
    switches = [a for a in w.done if a["type"] == "scene"]
    assert len(switches) == au.MAX_SCENE_SWITCHES                              # then paused
    assert any(h.result.startswith("loop protection") for h in e.history)


def test_failing_action_is_contained_and_queue_is_bounded():
    e, w = engine([{"id": "x", "cooldown_s": 0, "trigger": {"type": "manual"},
                    "action": {"type": "replay", "boom": True}}])
    e.run_rule("x")
    e.step(5.0)
    assert e.errors == 1 and e.history[-1].result.startswith("failed")
    for i in range(au.QUEUE_MAX + 50):
        e.emit("gesture", gesture=f"g{i}")
    assert e.dropped == 50
    e.step(6.0)                                                               # processes without error


def test_camera_disconnect_and_recording_edges():
    e, w = engine([{"id": "lost", "debounce_s": 0, "cooldown_s": 0,
                    "trigger": {"type": "camera_disconnected", "source": "cam1"},
                    "action": {"type": "layout", "layout": "single"}},
                   {"id": "rec", "debounce_s": 0, "cooldown_s": 0, "trigger": {"type": "recording_started"},
                    "action": {"type": "color_preset", "name": "Live"}}])
    w.snap = au.Snapshot(scene="A", sources={"cam1": True})
    e.step(0)
    w.snap = au.Snapshot(scene="A", sources={"cam1": False}, recording=True)
    e.step(1)
    assert {a["type"] for a in w.done} == {"layout", "color_preset"}


def test_engine_thread_and_the_app(paths):
    from vcstudio.app.application import AppContext
    ctx = AppContext(paths, configure_logging=False)
    try:
        ctx.profiles.create("Away")
        ctx.settings.set("automation", {"enabled": True, "rules": [
            {"id": "r1", "name": "go away", "cooldown_s": 0, "trigger": {"type": "manual"},
             "action": {"type": "scene", "name": "Away"}}]})
        assert ctx.automation.running and len(ctx.automation.rules) == 1
        ctx.automation.run_rule("r1")
        deadline = time.time() + 5
        while time.time() < deadline and ctx.profiles.active_name != "Away":
            time.sleep(0.05)
        assert ctx.profiles.active_name == "Away" and ctx.pipeline.scene.name == "Away"
        assert ctx.run_action({"type": "scene", "name": "Nope"}).startswith("no scene")
        assert ctx.run_action({"type": "privacy", "level": "screens"}) == "privacy screens"
        assert ctx.run_action({"type": "layout", "layout": "grid"}) == "layout grid"
        assert ctx.run_action({"type": "action", "id": "privacy_toggle"}) == "no window to run this action"
        assert ctx.run_action({"type": "action", "id": "rm -rf"}).startswith("unknown action")
        assert ctx.run_action({"type": "record", "do": "stop"}) == "was not recording"
        ctx.settings.set("automation.enabled", False)
        assert not ctx.automation.running
    finally:
        ctx.shutdown()
