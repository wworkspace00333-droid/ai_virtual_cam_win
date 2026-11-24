"""Automation tab: rule builder (trigger, condition, action, priority, cooldown) and execution history
(vcstudio/core/automation.py)."""
from __future__ import annotations

import time

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from vcstudio.core import automation as au
from vcstudio.i18n import tr
from vcstudio.ui.studio.common import muted
from vcstudio.ui.widgets import Card

TRIGGER_LABELS = {"person_detected": "Person appears", "person_lost": "Nobody in view", "people_count":
                  "Number of people (e.g. >= 2)", "object_detected": "Object appears (class name)",
                  "face_count_changed": "Number of faces changes", "gesture": "Gesture (open_palm, victory, hand_raised, …)",
                  "camera_disconnected": "Camera lost (main or cam1…)", "recording_started": "Recording starts",
                  "recording_stopped": "Recording stops", "timer": "Every N seconds", "hotkey":
                  "Automation hotkey 1-4", "manual": "Only when run by hand",
                  "looking_at_camera": "Looks at the camera (AI Vision gaze)",
                  "looking_away": "Looks away from the camera (AI Vision gaze)"}
ACTION_LABELS = {"scene": "Switch to scene", "action": "App action (id)", "privacy": "Privacy level",
                 "record": "Recording (start / stop)", "replay": "Save instant replay", "camera": "Show camera",
                 "layout": "Camera layout", "color_preset": "Colour preset", "effects": "Effects (on / off)"}
TRIGGER_HINT = {"people_count": ">= 2", "object_detected": "laptop", "gesture": "victory",
                "camera_disconnected": "main", "timer": "300", "hotkey": "1"}
ACTION_HINT = {"scene": "Scene name", "action": "privacy_toggle", "privacy": "faces", "record": "start",
               "camera": "cam1", "layout": "pip", "color_preset": "Preset name", "effects": "off"}


def parse_trigger(kind: str, text: str) -> dict:
    t = text.strip()
    d: dict = {"type": kind}
    if kind == "people_count":
        parts = t.replace(">=", ">= ").replace("<=", "<= ").replace("==", "== ").split()
        op = parts[0] if parts and parts[0] in (">=", "<=", "==") else ">="
        num = next((p for p in parts if p.isdigit()), "1")
        d.update(op=op, n=int(num))
    elif kind == "object_detected":
        d["label"] = t or "person"
    elif kind == "gesture":
        d["gesture"] = t
    elif kind == "camera_disconnected":
        d["source"] = t or "main"
    elif kind == "timer":
        d["every_s"] = float(t) if t.replace(".", "", 1).isdigit() else 60.0
    elif kind == "hotkey":
        d["slot"] = int(t) if t.isdigit() and 1 <= int(t) <= 4 else 1
    return d


def parse_action(kind: str, text: str) -> dict:
    t = text.strip()
    key = {"scene": "name", "action": "id", "privacy": "level", "record": "do", "camera": "id", "layout": "layout",
           "color_preset": "name"}.get(kind)
    if kind == "effects":
        return {"type": kind, "on": t.lower() not in ("off", "0", "no", "false", "выкл")}
    return {"type": kind, key: t} if key else {"type": kind}


def describe(d: dict) -> str:
    return " ".join(str(v) for k, v in d.items() if k != "type") or ""


class AutomationTab:
    def __init__(self, ctx):
        self.ctx = ctx
        self.widget = QWidget()
        lay = QVBoxLayout(self.widget)
        top = Card("Automation")
        top.layout_.addWidget(muted("Rules switch scenes or run actions when something happens. A rule waits for its "
                                    "cooldown before it runs again; state triggers must hold for the debounce time; "
                                    "too many automatic scene switches in a row pause scene rules for 30 s."))
        self.enabled = QCheckBox(tr("Run automation rules"))
        top.layout_.addWidget(self.enabled)
        lay.addWidget(top)
        b = Card("New rule")
        g = QGridLayout()
        self.name = QLineEdit()
        self.trigger = QComboBox()
        for k in au.TRIGGERS:
            self.trigger.addItem(tr(TRIGGER_LABELS[k]), k)
        self.trigger_arg = QLineEdit()
        self.scene_cond = QComboBox()
        self.action = QComboBox()
        for k in au.ACTIONS:
            self.action.addItem(tr(ACTION_LABELS[k]), k)
        self.action_arg = QLineEdit()
        self.cond_text = QLineEdit()
        self.cond_text.setPlaceholderText("people>=2 and not recording · gaze or time 09:00-18:00")
        self.priority = QSpinBox()
        self.priority.setRange(-100, 100)
        self.cooldown = QDoubleSpinBox()
        self.cooldown.setRange(0, 3600)
        self.cooldown.setValue(10)
        self.debounce = QDoubleSpinBox()
        self.debounce.setRange(0, 60)
        self.debounce.setValue(1.5)
        rows = [("Name", self.name), ("When", self.trigger), ("Value", self.trigger_arg),
                ("Only in scene", self.scene_cond), ("Do", self.action), ("Value", self.action_arg),
                ("Priority", self.priority), ("Cooldown, s", self.cooldown), ("Debounce, s", self.debounce),
                ("And if", self.cond_text)]
        for i, (lbl, w) in enumerate(rows):
            g.addWidget(QLabel(tr(lbl)), i // 3, (i % 3) * 2)
            g.addWidget(w, i // 3, (i % 3) * 2 + 1)
        b.layout_.addLayout(g)
        self.add_btn = QPushButton(tr("Add rule"))
        b.layout_.addWidget(self.add_btn)
        self.msg = QLabel()
        self.msg.setObjectName("muted")
        b.layout_.addWidget(self.msg)
        lay.addWidget(b)
        rl = Card("Rules")
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([tr(h) for h in ("Name", "When", "Do", "Condition", "Priority", "Cooldown",
                                                               "Enabled")])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setMinimumHeight(120)
        rl.layout_.addWidget(self.table)
        r = QHBoxLayout()
        self.toggle_btn = QPushButton(tr("Enable / disable"))
        self.run_btn = QPushButton(tr("Run now"))
        self.remove_btn = QPushButton(tr("Remove"))
        for w in (self.toggle_btn, self.run_btn, self.remove_btn):
            r.addWidget(w)
        r.addStretch(1)
        rl.layout_.addLayout(r)
        lay.addWidget(rl)
        hi = Card("History")
        self.hist = QTableWidget(0, 5)
        self.hist.setHorizontalHeaderLabels([tr(h) for h in ("Time", "Rule", "Trigger", "Action", "Result")])
        self.hist.horizontalHeader().setStretchLastSection(True)
        self.hist.verticalHeader().setVisible(False)
        self.hist.setEditTriggers(QTableWidget.NoEditTriggers)
        self.hist.setMinimumHeight(120)
        hi.layout_.addWidget(self.hist)
        lay.addWidget(hi)
        lay.addStretch(1)
        self._loading = False
        self.load()
        self.enabled.toggled.connect(self._save_enabled)
        self.trigger.currentIndexChanged.connect(self._hints)
        self.action.currentIndexChanged.connect(self._hints)
        self.add_btn.clicked.connect(lambda: self.add_rule())
        self.toggle_btn.clicked.connect(self.toggle)
        self.run_btn.clicked.connect(self.run_now)
        self.remove_btn.clicked.connect(self.remove)
        self._hints()

    def _cfg(self) -> dict:
        c = self.ctx.settings.get("automation") or {}
        return {"enabled": bool(c.get("enabled")), "rules": list(c.get("rules") or [])}

    def _hints(self, *_):
        self.trigger_arg.setPlaceholderText(TRIGGER_HINT.get(self.trigger.currentData(), ""))
        self.trigger_arg.setEnabled(self.trigger.currentData() in TRIGGER_HINT)
        self.action_arg.setPlaceholderText(tr(ACTION_HINT.get(self.action.currentData(), "")))
        self.action_arg.setEnabled(self.action.currentData() in ACTION_HINT)

    def load(self) -> None:
        self._loading = True
        try:
            c = self._cfg()
            self.enabled.setChecked(c["enabled"])
            self.scene_cond.clear()
            self.scene_cond.addItem(tr("Any scene"), "")
            for n in self.ctx.profiles.names():
                self.scene_cond.addItem(n, n)
            self.table.setRowCount(len(c["rules"]))
            for row, r in enumerate(c["rules"]):
                trig, act = r.get("trigger", {}), r.get("action", {})
                vals = [r.get("name") or r.get("id"),
                        tr(TRIGGER_LABELS.get(trig.get("type"), "?")) + (" " + describe(trig) if describe(trig) else ""),
                        tr(ACTION_LABELS.get(act.get("type"), "?")) + (" " + describe(act) if describe(act) else ""),
                        " · ".join(x for x in ((r.get("condition") or {}).get("scene") or "",
                                               au.describe_condition(r.get("condition") or {})) if x) or tr("any"),
                        r.get("priority", 0),
                        f"{r.get('cooldown_s', 5)} s", tr("yes") if r.get("enabled", True) else tr("no")]
                for col, v in enumerate(vals):
                    self.table.setItem(row, col, QTableWidgetItem(str(v)))
        finally:
            self._loading = False

    def _store(self, c: dict) -> None:
        self.ctx.settings.set("automation", c)
        self.load()

    def _save_enabled(self, on: bool) -> None:
        if self._loading:
            return
        c = self._cfg()
        c["enabled"] = bool(on)
        self._store(c)

    def add_rule(self, rule: dict | None = None) -> str | None:
        if rule is None:
            try:
                group = au.parse_condition(self.cond_text.text())
            except ValueError as e:
                self.msg.setText(tr("Condition: {e}", e=e))
                return None
            rule = {"name": self.name.text().strip(),
                    "trigger": parse_trigger(self.trigger.currentData(), self.trigger_arg.text()),
                    "action": parse_action(self.action.currentData(), self.action_arg.text()),
                    "condition": {"scene": self.scene_cond.currentData() or "", **group},
                    "priority": self.priority.value(), "cooldown_s": self.cooldown.value(),
                    "debounce_s": self.debounce.value(), "enabled": True}
        if rule["action"].get("type") == "scene" and rule["action"].get("name") not in self.ctx.profiles.names():
            self.msg.setText(tr("No scene with that name."))
            return None
        c = self._cfg()
        used = {r.get("id") for r in c["rules"]}
        rid = next(f"rule{i}" for i in range(1, 1000) if f"rule{i}" not in used)
        rule = {**rule, "id": rid, "name": rule.get("name") or rid}
        if au.Rule.from_dict(rule) is None:
            self.msg.setText(tr("This rule is not complete."))
            return None
        c["rules"].append(rule)
        self._store(c)
        self.msg.setText(tr("Rule added."))
        return rid

    def _selected(self) -> int:
        return self.table.currentRow()

    def toggle(self) -> None:
        c = self._cfg()
        i = self._selected()
        if 0 <= i < len(c["rules"]):
            c["rules"][i] = {**c["rules"][i], "enabled": not c["rules"][i].get("enabled", True)}
            self._store(c)

    def run_now(self) -> None:
        c = self._cfg()
        i = self._selected()
        if 0 <= i < len(c["rules"]):
            if not self.ctx.automation.running:
                self.msg.setText(tr("Turn automation on first."))
                return
            self.ctx.automation.run_rule(c["rules"][i]["id"])

    def remove(self) -> None:
        c = self._cfg()
        i = self._selected()
        if 0 <= i < len(c["rules"]):
            c["rules"].pop(i)
            self._store(c)

    def tick(self) -> None:
        items = list(self.ctx.automation.history)[-30:][::-1]
        self.hist.setRowCount(len(items))
        now_m, now_w = time.monotonic(), time.time()
        for row, h in enumerate(items):
            when = time.strftime("%H:%M:%S", time.localtime(now_w - (now_m - h.t)))
            for col, v in enumerate((when, h.rule, h.trigger, h.action, h.result)):
                self.hist.setItem(row, col, QTableWidgetItem(str(v)))
