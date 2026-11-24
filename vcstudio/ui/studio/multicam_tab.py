"""Multi-Camera tab: extra sources and the layout of the active scene (vcstudio/core/multicam.py)."""
from __future__ import annotations

import copy

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel,
                               QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from vcstudio.core import multicam as mc
from vcstudio.i18n import tr
from vcstudio.ui.studio.common import muted
from vcstudio.ui.widgets import Card

LAYOUT_LABELS = {"single": "Single camera", "pip": "Picture-in-picture", "side_by_side": "Side by side",
                 "split_v": "Split (top / bottom)", "grid": "2 × 2 grid", "custom": "Custom tiles"}
KINDS = [("camera", "Camera"), ("screen", "Screen or window"), ("video_file", "Video file"),
         ("test_pattern", "Test pattern")]
MISSING_LABELS = {"placeholder": "Show “no signal”", "hold": "Hold the last picture (3 s)", "hide": "Leave it out"}


class MulticamTab:
    def __init__(self, ctx):
        self.ctx = ctx
        self.widget = QWidget()
        lay = QVBoxLayout(self.widget)
        top = Card("Layout of this scene")
        top.layout_.addWidget(muted("Extra sources are drawn on the finished picture. The main input (Input page) "
                                    "keeps pacing the camera: a slow or lost extra source never slows it down."))
        g = QGridLayout()
        self.enabled = QCheckBox(tr("Use extra cameras in this scene"))
        g.addWidget(self.enabled, 0, 0, 1, 4)
        self.layout_box = QComboBox()
        for k in mc.LAYOUTS:
            self.layout_box.addItem(tr(LAYOUT_LABELS[k]), k)
        self.primary = QComboBox()
        self.corner = QComboBox()
        for k, lbl in (("bottom_right", "Bottom right"), ("bottom_left", "Bottom left"), ("top_right", "Top right"),
                       ("top_left", "Top left")):
            self.corner.addItem(tr(lbl), k)
        self.size = QDoubleSpinBox()
        self.size.setRange(0.1, 0.5)
        self.size.setSingleStep(0.02)
        self.missing = QComboBox()
        for k in mc.MISSING:
            self.missing.addItem(tr(MISSING_LABELS[k]), k)
        self.auto = QComboBox()
        self.auto.addItem(tr("Off"), "off")
        self.auto.addItem(tr("Switch when nobody is on the main camera"), "people")
        self.auto.addItem(tr("Show who is speaking (mouth movement; needs the face models)"), "speaker")
        self.fallback = QComboBox()
        self.transition = QComboBox()
        for k, lbl in (("cut", "Cut"), ("fade", "Cross-fade"), ("slide", "Slide")):
            self.transition.addItem(tr(lbl), k)
        self.transition_s = QDoubleSpinBox()
        self.transition_s.setRange(0.05, 3.0)
        self.transition_s.setSingleStep(0.1)
        rows = [("Layout", self.layout_box), ("Main picture", self.primary), ("Window corner", self.corner),
                ("Window size", self.size), ("Lost source", self.missing), ("Automatic switching", self.auto),
                ("Switch to", self.fallback), ("Transition", self.transition), ("Transition time, s", self.transition_s)]
        for i, (label, w) in enumerate(rows):
            g.addWidget(QLabel(tr(label)), 1 + i // 2, (i % 2) * 2)
            g.addWidget(w, 1 + i // 2, (i % 2) * 2 + 1)
        top.layout_.addLayout(g)
        lay.addWidget(top)
        src = Card("Extra sources")
        src.layout_.addWidget(muted("Up to {n} besides the main input. This app's own camera cannot be used (it would "
                                    "show itself).", n=mc.MAX_SOURCES))
        r = QHBoxLayout()
        self.kind = QComboBox()
        for k, lbl in KINDS:
            self.kind.addItem(tr(lbl), k)
        self.device = QComboBox()
        self.device.setEditable(True)
        self.device.setMinimumWidth(220)
        self.browse = QPushButton(tr("Choose…"))
        self.add_btn = QPushButton(tr("Add source"))
        for w in (self.kind, self.device, self.browse, self.add_btn):
            r.addWidget(w)
        r.setStretch(1, 1)
        src.layout_.addLayout(r)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([tr(h) for h in ("Id", "Source", "State", "FPS", "Size", "Age")])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setMinimumHeight(110)
        src.layout_.addWidget(self.table)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel(tr("Selected camera: colour preset")))
        self.src_color = QComboBox()
        r1.addWidget(self.src_color)
        r1.addWidget(QLabel(tr("Privacy")))
        self.src_privacy = QComboBox()
        for k, lbl in (("shield", "Privacy shield checks it"), ("hide", "Always hidden whole"),
                       ("off", "Not checked")):
            self.src_privacy.addItem(tr(lbl), k)
        r1.addWidget(self.src_privacy)
        r1.addStretch(1)
        src.layout_.addLayout(r1)
        r2 = QHBoxLayout()
        self.remove_btn = QPushButton(tr("Remove source"))
        self.next_cam = QPushButton(tr("Next camera"))
        self.next_layout = QPushButton(tr("Next layout"))
        for w in (self.remove_btn, self.next_cam, self.next_layout):
            r2.addWidget(w)
        r2.addStretch(1)
        src.layout_.addLayout(r2)
        self.status = QLabel("—")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        src.layout_.addWidget(self.status)
        lay.addWidget(src)
        lay.addStretch(1)
        self._loading = False
        self.load()
        for w in (self.enabled,):
            w.toggled.connect(self.save)
        for w in (self.layout_box, self.primary, self.corner, self.missing, self.auto, self.fallback,
                  self.transition):
            w.currentIndexChanged.connect(self.save)
        self.size.valueChanged.connect(self.save)
        self.transition_s.valueChanged.connect(self.save)
        self.kind.currentIndexChanged.connect(self._fill_devices)
        self.table.currentCellChanged.connect(lambda *_: self._show_source())
        self.src_color.currentIndexChanged.connect(self._save_source)
        self.src_privacy.currentIndexChanged.connect(self._save_source)
        self.browse.clicked.connect(self._browse)
        self.add_btn.clicked.connect(lambda: self.add_source())
        self.remove_btn.clicked.connect(self.remove_source)
        self.next_cam.clicked.connect(lambda: ctx.multicam.next_camera())
        self.next_layout.clicked.connect(lambda: ctx.multicam.next_layout())
        ctx.profiles.add_listener(lambda ev, _n: self.load() if ev in ("switched",) else None)
        self._fill_devices()

    # ------------------------------------------------------------------ profile <-> widgets
    def _data(self) -> dict:
        return copy.deepcopy(self.ctx.profiles.active()["body"].get("multicam") or {})

    def load(self) -> None:
        self._loading = True
        try:
            c = mc.MulticamConfig.from_profile(self.ctx.profiles.active()["body"])
            self.enabled.setChecked(c.enabled)
            self.layout_box.setCurrentIndex(max(0, self.layout_box.findData(c.layout)))
            ids = ["main"] + [s["id"] for s in c.sources]
            for box, cur, with_main in ((self.primary, c.primary, True), (self.fallback, c.fallback, False)):
                box.blockSignals(True)
                box.clear()
                if not with_main:
                    box.addItem(tr("—"), "")
                for sid in ids if with_main else ids[1:]:
                    box.addItem(tr("Main input") if sid == "main" else sid, sid)
                box.setCurrentIndex(max(0, box.findData(cur)))
                box.blockSignals(False)
            self.corner.setCurrentIndex(max(0, self.corner.findData(c.pip_corner)))
            self.size.setValue(c.pip_size)
            self.missing.setCurrentIndex(max(0, self.missing.findData(c.missing)))
            self.auto.setCurrentIndex(max(0, self.auto.findData(c.auto)))
            self.transition.setCurrentIndex(max(0, self.transition.findData(c.transition)))
            self.transition_s.setValue(c.transition_s)
            self._sources = [dict(s) for s in c.sources]
            self._refresh_table()
            self.src_color.blockSignals(True)
            self.src_color.clear()
            self.src_color.addItem(tr("None"), "")
            for n in sorted((self.ctx.settings.get("color_presets") or {}).keys()):
                self.src_color.addItem(n, n)
            self.src_color.blockSignals(False)
            self._show_source()
        finally:
            self._loading = False

    def save(self, *_):
        if self._loading:
            return
        d = self._data()
        d.update(enabled=self.enabled.isChecked(), layout=self.layout_box.currentData(),
                 primary=self.primary.currentData() or "main", pip_corner=self.corner.currentData(),
                 pip_size=round(self.size.value(), 3), missing=self.missing.currentData(),
                 auto=self.auto.currentData(), fallback=self.fallback.currentData() or "",
                 transition=self.transition.currentData(), transition_s=round(self.transition_s.value(), 2),
                 sources=self._sources)
        self.ctx.update_active_profile(lambda b: b.__setitem__("multicam", d))

    # ------------------------------------------------------------------ sources
    def _fill_devices(self, *_):
        kind = self.kind.currentData()
        self.device.clear()
        self.browse.setVisible(kind == "video_file")
        self.device.setEnabled(kind != "test_pattern")
        if kind == "camera":
            try:
                from vcstudio.core.devices import list_video_devices
                for d in list_video_devices():
                    if not d.is_own_output:
                        self.device.addItem(d.name, d.unique_id)
            except Exception as e:                 # noqa: BLE001 - listing is best effort
                self.status.setText(tr("Cannot list cameras: {e}", e=e))
        elif kind == "screen":
            try:
                from vcstudio.platform.win import screen_capture as sc
                for m in sc.monitors():
                    self.device.addItem(tr("Screen {n} ({w}×{h})", n=m.index + 1, w=m.width, h=m.height),
                                        f"monitor:{m.index}")
                for w in sc.windows():
                    title = w.title if len(w.title) <= 70 else w.title[:67] + "…"
                    self.device.addItem(tr("Window: {title}", title=title), f"window:{w.title}")
            except Exception:                      # noqa: BLE001 - not on this platform
                self.device.addItem(tr("Screen {n} ({w}×{h})", n=1, w="?", h="?"), "monitor:0")

    def _browse(self):
        f, _ = QFileDialog.getOpenFileName(self.widget, tr("Choose a video"), "", tr("Videos (*.mp4 *.mov *.mkv *.avi)"))
        if f:
            self.device.setEditText(f)

    def add_source(self, spec: dict | None = None) -> str | None:
        if len(self._sources) >= mc.MAX_SOURCES:
            self.status.setText(tr("At most {n} extra sources.", n=mc.MAX_SOURCES))
            return None
        if spec is None:
            kind = self.kind.currentData()
            text, data = self.device.currentText().strip(), self.device.currentData()
            if kind == "camera":
                from vcstudio.platform import DEFAULT_DEVICE_METHOD
                spec = {"method": DEFAULT_DEVICE_METHOD, "device_unique_id": data, "device_name_hint": text}
            elif kind == "screen":
                spec = {"method": "screen", "screen_target": data or text or "monitor:0"}
            elif kind == "video_file":
                if not text:
                    self.status.setText(tr("Choose a video file first."))
                    return None
                spec = {"method": "video_file", "video_path": text, "video_loop": True}
            else:
                spec = {"method": "test_pattern"}
            spec["name"] = text or tr(dict(KINDS)[kind])
        used = {s["id"] for s in self._sources}
        sid = next(f"cam{i}" for i in range(1, 10) if f"cam{i}" not in used)
        self._sources.append({"id": sid, "enabled": True, **spec})
        self.save()
        self.load()
        return sid

    def _show_source(self) -> None:
        row = self.table.currentRow()
        ok = 0 <= row < len(self._sources)
        for w in (self.src_color, self.src_privacy):
            w.setEnabled(ok)
            w.blockSignals(True)
        if ok:
            s = self._sources[row]
            self.src_color.setCurrentIndex(max(0, self.src_color.findData(s.get("color_preset") or "")))
            self.src_privacy.setCurrentIndex(max(0, self.src_privacy.findData(s.get("privacy") or "shield")))
        for w in (self.src_color, self.src_privacy):
            w.blockSignals(False)

    def _save_source(self, *_) -> None:
        row = self.table.currentRow()
        if self._loading or not 0 <= row < len(self._sources):
            return
        self._sources[row]["color_preset"] = self.src_color.currentData() or ""
        self._sources[row]["privacy"] = self.src_privacy.currentData()
        self.save()

    def remove_source(self):
        row = self.table.currentRow()
        if 0 <= row < len(self._sources):
            self._sources.pop(row)
            self.save()
            self.load()

    def _refresh_table(self):
        st = self.ctx.multicam.sources.stats()
        errs = self.ctx.multicam.sources.errors
        self.table.setRowCount(len(self._sources))
        for row, s in enumerate(self._sources):
            cs = st.get(s["id"])
            vals = [s["id"], s.get("name") or s.get("method"),
                    tr(cs.state.replace("_", " ")) if cs else (errs.get(s["id"]) or tr("stopped")),
                    f"{cs.measured_fps:.0f}" if cs else "—", f"{cs.width}×{cs.height}" if cs and cs.width else "—",
                    f"{cs.last_frame_age_ms:.0f} ms" if cs and cs.frames else "—"]
            for col, v in enumerate(vals):
                self.table.setItem(row, col, QTableWidgetItem(str(v)))

    def tick(self):
        self._refresh_table()
        s = self.ctx.multicam.stats
        self.status.setText(tr("Showing: {p} · composition {ms} ms · stale tiles {n} · auto switches {a}",
                               p=tr("Main input") if s.primary == "main" else s.primary, ms=f"{s.compose_ms:.1f}",
                               n=s.stale_tiles, a=self.ctx.multicam.auto.switches))
        sp = self.ctx.multicam.speaker
        if sp is not None and sp.stats.state != "stopped":
            st = sp.stats
            act = ", ".join(f"{tr('Main input') if k == 'main' else k} {v:.3f}" for k, v in st.activity.items())
            self.status.setText(self.status.text() + "\n" + (
                tr("Speaker detection: models missing (AI Models)") if st.state == "no_models" else
                tr("Speaker detection: {state} · speaking: {who} · mouth movement: {act}", state=tr(st.state),
                   who=st.speaker or "—", act=act or "—")))
