"""Privacy Shield tab: privacy mode levels (existing) + text regions, patterns, custom areas, strict fail-safe."""
from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QPushButton, QVBoxLayout, QWidget)

from vcstudio.i18n import tr
from vcstudio.replacement import privacy_shield as ps
from vcstudio.ui.studio.common import Worker, muted
from vcstudio.ui.widgets import Card

PATTERN_LABELS = {"email": "E-mail addresses", "phone": "Phone numbers", "card": "Payment card numbers",
                  "account": "Account-like numbers (IBAN, long numbers)", "address": "Street addresses",
                  "keywords": "My keywords"}


class PrivacyTab:
    def __init__(self, ctx):
        self.ctx = ctx
        self.widget = QWidget()
        lay = QVBoxLayout(self.widget)
        lvl = Card("Privacy mode (faces and screens)")
        lvl.layout_.addWidget(muted("Hides detected faces, then also monitors, laptops, phones and books, then also "
                                    "blurs the background (hotkey Ctrl+Alt+H)."))
        self.level = QComboBox()
        for k, v in (("off", "Off"), ("faces", "Faces"), ("screens", "Faces and screens"),
                     ("all", "Faces, screens and background")):
            self.level.addItem(tr(v), k)
        lvl.layout_.addWidget(self.level)
        lay.addWidget(lvl)
        sh = Card("Privacy shield (text and areas)")
        sh.layout_.addWidget(muted("Finds text in the picture on this PC and hides it, plus the areas you mark. It "
                                   "reduces what leaves the PC; it is not a guarantee: small (under about 12 px), "
                                   "slanted, handwritten or faint text is often missed, and e-mail addresses are "
                                   "recognised only by hiding all text. Extra cameras of a layout are not covered."))
        g = QGridLayout()
        self.enabled = QCheckBox(tr("Use the privacy shield"))
        g.addWidget(self.enabled, 0, 0, 1, 4)
        self.text = QComboBox()
        for k, v in (("off", "Do not look for text"), ("all", "Hide all text"),
                     ("sensitive", "Hide only sensitive text")):
            self.text.addItem(tr(v), k)
        self.effect = QComboBox()
        for k, v in (("pixelate", "Pixelate"), ("blur", "Blur"), ("black", "Black box")):
            self.effect.addItem(tr(v), k)
        self.padding = QDoubleSpinBox()
        self.padding.setRange(0, 2)
        self.padding.setSingleStep(0.05)
        self.sens = QDoubleSpinBox()
        self.sens.setRange(0, 1)
        self.sens.setSingleStep(0.1)
        self.strict = QCheckBox(tr("Strict: keep areas hidden while detection is late or failing"))
        rows = [("Text", self.text), ("Effect", self.effect), ("Padding (× text height)", self.padding),
                ("Sensitivity", self.sens)]
        for i, (lbl, w) in enumerate(rows):
            g.addWidget(QLabel(tr(lbl)), 1 + i // 2, (i % 2) * 2)
            g.addWidget(w, 1 + i // 2, (i % 2) * 2 + 1)
        g.addWidget(self.strict, 3, 0, 1, 4)
        sh.layout_.addLayout(g)
        self.pattern_boxes = {}
        pr = QGridLayout()
        for i, (k, v) in enumerate(PATTERN_LABELS.items()):
            cb = QCheckBox(tr(v))
            self.pattern_boxes[k] = cb
            pr.addWidget(cb, i // 2, i % 2)
        sh.layout_.addLayout(pr)
        self.keywords = QLineEdit()
        self.keywords.setPlaceholderText(tr("Keywords, separated by commas"))
        sh.layout_.addWidget(self.keywords)
        self.rec_note = QLabel()
        self.rec_note.setObjectName("muted")
        self.rec_note.setWordWrap(True)
        sh.layout_.addWidget(self.rec_note)
        self.dl_btn = QPushButton(tr("Download the text detection model (3 MB)"))
        sh.layout_.addWidget(self.dl_btn)
        self.worker = Worker()
        self.worker.done.connect(self._downloaded)
        lay.addWidget(sh)
        ar = Card("Areas")
        ar.layout_.addWidget(muted("Always hidden areas and areas never hidden by text detection, in shares of the "
                                   "picture: x, y, width, height (0..1)."))
        r = QHBoxLayout()
        self.area = QLineEdit()
        self.area.setPlaceholderText("0.70, 0.05, 0.25, 0.10")
        self.add_hide = QPushButton(tr("Always hide"))
        self.add_excl = QPushButton(tr("Never hide"))
        for w in (self.area, self.add_hide, self.add_excl):
            r.addWidget(w)
        r.setStretch(0, 1)
        ar.layout_.addLayout(r)
        self.areas = QListWidget()
        self.areas.setMaximumHeight(110)
        ar.layout_.addWidget(self.areas)
        self.rm_area = QPushButton(tr("Remove area"))
        ar.layout_.addWidget(self.rm_area)
        lay.addWidget(ar)
        self.status = QLabel("—")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        lay.addStretch(1)
        self._loading = False
        self.load()
        self.level.currentIndexChanged.connect(self._set_level)
        for w in (self.enabled, self.strict, *self.pattern_boxes.values()):
            w.toggled.connect(self.save)
        for w in (self.text, self.effect):
            w.currentIndexChanged.connect(self.save)
        for w in (self.padding, self.sens):
            w.valueChanged.connect(self.save)
        self.keywords.editingFinished.connect(self.save)
        self.add_hide.clicked.connect(lambda: self.add_area("regions"))
        self.add_excl.clicked.connect(lambda: self.add_area("exclude"))
        self.rm_area.clicked.connect(self.remove_area)
        self.dl_btn.clicked.connect(self.download)
        ctx.profiles.add_listener(lambda ev, _n: self._load_level() if ev == "switched" else None)

    def _load_level(self) -> None:
        from vcstudio.replacement import privacy_mode as pm
        self.level.blockSignals(True)
        self.level.setCurrentIndex(max(0, self.level.findData(pm.level_of(self.ctx.profiles.active()["body"]))))
        self.level.blockSignals(False)

    def _set_level(self, *_):
        from vcstudio.replacement import privacy_mode as pm
        lv = self.level.currentData()
        self.ctx.update_active_profile(lambda b: pm.set_level(b, lv))
        if lv != "off" and not self.ctx.pipeline.replacement_enabled:
            self.ctx.pipeline.set_replacement_enabled(True)

    def _data(self) -> dict:
        return dict(self.ctx.settings.get("privacy_shield"))

    def load(self) -> None:
        self._loading = True
        try:
            self._load_level()
            d = self._data()
            c = ps.ShieldConfig.from_settings(self.ctx.settings)
            self.enabled.setChecked(c.enabled)
            self.text.setCurrentIndex(max(0, self.text.findData(c.text)))
            self.effect.setCurrentIndex(max(0, self.effect.findData(c.effect)))
            self.padding.setValue(c.padding)
            self.sens.setValue(c.sensitivity)
            self.strict.setChecked(c.strict)
            for k, cb in self.pattern_boxes.items():
                cb.setChecked(k in c.patterns)
            self.keywords.setText(", ".join(c.keywords))
            self.areas.clear()
            for kind in ("regions", "exclude"):
                for a in d.get(kind) or []:
                    label = tr("hide") if kind == "regions" else tr("never hide")
                    self.areas.addItem(f"{label}: {a.get('x', 0):.2f}, {a.get('y', 0):.2f}, {a.get('w', 0):.2f}, "
                                       f"{a.get('h', 0):.2f}")
            from vcstudio.ai import text_detect as td
            notes = [tr("Text detection: ready.") if td.available() else
                     tr("Text detection model not downloaded.")]
            if c.text == "sensitive" and not td.recognizer_available():
                notes.append(tr("“Only sensitive text” needs the text recognition model (not downloaded): until "
                                "then all text is hidden."))
            self.rec_note.setText(" ".join(notes))
            from vcstudio.ai.model_download import network_allowed
            need_rec = c.text == "sensitive" and not td.recognizer_available()
            self.dl_btn.setText(tr("Download the text detection model (3 MB)") if not td.available() else
                                tr("Download the text recognition model (16 MB)"))
            self.dl_btn.setVisible((not td.available() or need_rec) and network_allowed(self.ctx.settings))
        finally:
            self._loading = False

    def save(self, *_):
        if self._loading:
            return
        d = self._data()
        d.update(enabled=self.enabled.isChecked(), text=self.text.currentData(), effect=self.effect.currentData(),
                 padding=round(self.padding.value(), 3), sensitivity=round(self.sens.value(), 3),
                 strict=self.strict.isChecked(),
                 patterns=[k for k, cb in self.pattern_boxes.items() if cb.isChecked()],
                 keywords=[k.strip() for k in self.keywords.text().split(",") if k.strip()])
        self.ctx.settings.set("privacy_shield", d)
        self.load()

    def add_area(self, kind: str, text: str | None = None) -> bool:
        try:
            x, y, w, h = (float(v) for v in (text if text is not None else self.area.text()).split(","))
        except ValueError:
            self.status.setText(tr("Enter four numbers: x, y, width, height (0..1)."))
            return False
        d = self._data()
        d[kind] = list(d.get(kind) or []) + [{"x": x, "y": y, "w": w, "h": h}]
        self.ctx.settings.set("privacy_shield", d)
        self.load()
        return True

    def remove_area(self) -> None:
        row = self.areas.currentRow()
        d = self._data()
        regs, excl = list(d.get("regions") or []), list(d.get("exclude") or [])
        if 0 <= row < len(regs):
            regs.pop(row)
        elif 0 <= row - len(regs) < len(excl):
            excl.pop(row - len(regs))
        else:
            return
        d.update(regions=regs, exclude=excl)
        self.ctx.settings.set("privacy_shield", d)
        self.load()

    def download(self) -> None:
        from vcstudio.ai import model_download as md
        from vcstudio.ai.open_vocab import user_models_dir
        self.dl_btn.setEnabled(False)
        self.rec_note.setText(tr("Downloading…"))
        from vcstudio.ai import text_detect as td
        table = md.TEXT if not td.available() else md.TEXT_REC
        self.worker.run("download", lambda: md.download(user_models_dir(), table=table))

    def _downloaded(self, kind, res, err) -> None:
        self.dl_btn.setEnabled(True)
        if err is not None:
            self.rec_note.setText(tr("Download failed: {e}", e=err))
            return
        self.load()
        self.ctx.shield.stop()
        self.ctx.shield.apply_settings()

    def tick(self) -> None:
        s = self.ctx.shield.stats
        self.status.setText(tr("Shield: {st} · text boxes {b} · hidden now {h} · detection {ms} ms",
                               st=tr(s.state.replace("_", " ")), b=s.boxes, h=s.hidden, ms=f"{s.ms:.0f}")
                            + (("\n" + ", ".join(f"{k}: {v}" for k, v in s.kinds.items())) if s.kinds else "")
                            + (f"\n{s.error}" if s.error else ""))
