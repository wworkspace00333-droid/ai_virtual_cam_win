"""Colour Studio tab: grading controls and LUTs of the active scene (vcstudio/replacement/color.py), and colour
presets (saved in settings "color_presets", applied to any scene)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QSlider, QVBoxLayout, QWidget)

from vcstudio.i18n import tr
from vcstudio.replacement import color as cs
from vcstudio.ui.studio.common import muted
from vcstudio.ui.widgets import Card

LABELS = {"exposure": "Exposure", "brightness": "Brightness", "contrast": "Contrast", "saturation": "Saturation",
          "temperature": "Temperature", "tint": "Tint", "gamma": "Gamma", "highlights": "Highlights",
          "shadows": "Shadows", "sharpness": "Sharpness", "vignette": "Vignette", "grain": "Film grain",
          "glow": "Soft glow", "lut_intensity": "LUT intensity"}
RES = 0.01                      # slider resolution: every control moves in steps of 0.01 (neutral values exact)


def steps(name: str) -> int:
    lo, hi, _ = cs.RANGES[name]
    return int(round((hi - lo) / RES))


class ColorTab:
    def __init__(self, ctx):
        self.ctx = ctx
        self.luts_dir: Path = ctx.paths.root / "luts"
        self.widget = QWidget()
        lay = QVBoxLayout(self.widget)
        c = Card("Colour of this scene")
        c.layout_.addWidget(muted("Applied once to the camera picture, after automatic lighting and before "
                                  "backgrounds and replacements. Runs on the processor: everything at once costs "
                                  "about 55 ms per 1080p frame on a 2-core PC; tone and saturation alone a few ms."))
        self.enabled = QCheckBox(tr("Grade this scene"))
        c.layout_.addWidget(self.enabled)
        g = QGridLayout()
        self.sliders: dict[str, QSlider] = {}
        self.values: dict[str, QLabel] = {}
        for i, (name, label) in enumerate(LABELS.items()):
            lo, hi, neutral = cs.RANGES[name]
            s = QSlider(Qt.Horizontal)
            s.setRange(0, steps(name))
            s.setProperty("name", name)
            v = QLabel()
            v.setMinimumWidth(42)
            self.sliders[name], self.values[name] = s, v
            row, col = i // 2, (i % 2) * 3
            g.addWidget(QLabel(tr(label)), row, col)
            g.addWidget(s, row, col + 1)
            g.addWidget(v, row, col + 2)
        c.layout_.addLayout(g)
        self.skin = QCheckBox(tr("Protect skin tones from saturation changes"))
        c.layout_.addWidget(self.skin)
        r = QHBoxLayout()
        self.lut = QComboBox()
        self.import_btn = QPushButton(tr("Import .cube…"))
        self.reset_btn = QPushButton(tr("Reset"))
        r.addWidget(QLabel(tr("LUT")))
        r.addWidget(self.lut, 1)
        r.addWidget(self.import_btn)
        r.addWidget(self.reset_btn)
        c.layout_.addLayout(r)
        lay.addWidget(c)
        pr = Card("Presets")
        r2 = QHBoxLayout()
        self.preset = QComboBox()
        self.preset_name = QLineEdit()
        self.preset_name.setPlaceholderText(tr("Preset name"))
        self.save_preset_btn = QPushButton(tr("Save as preset"))
        self.apply_preset_btn = QPushButton(tr("Apply preset"))
        for w in (self.preset, self.apply_preset_btn, self.preset_name, self.save_preset_btn):
            r2.addWidget(w)
        r2.setStretch(0, 1)
        r2.setStretch(2, 1)
        pr.layout_.addLayout(r2)
        lay.addWidget(pr)
        self.status = QLabel("—")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        lay.addStretch(1)
        self._loading = False
        self.load()
        self.enabled.toggled.connect(self.save)
        self.skin.toggled.connect(self.save)
        for s in self.sliders.values():
            s.valueChanged.connect(self.save)
        self.lut.currentIndexChanged.connect(self.save)
        self.import_btn.clicked.connect(lambda: self.import_lut())
        self.reset_btn.clicked.connect(self.reset)
        self.save_preset_btn.clicked.connect(lambda: self.save_preset())
        self.apply_preset_btn.clicked.connect(lambda: self.apply_preset())
        ctx.profiles.add_listener(lambda ev, _n: self.load() if ev == "switched" else None)

    # ------------------------------------------------------------------ value <-> slider
    @staticmethod
    def _to_slider(name: str, v: float) -> int:
        lo, _hi, _ = cs.RANGES[name]
        return int(round((v - lo) / RES))

    @staticmethod
    def _from_slider(name: str, pos: int) -> float:
        lo, _hi, _ = cs.RANGES[name]
        return round(lo + pos * RES, 2)

    def _fill_luts(self, current: str) -> None:
        self.lut.blockSignals(True)
        self.lut.clear()
        self.lut.addItem(tr("None"), "")
        files = sorted(self.luts_dir.glob("*.cube")) if self.luts_dir.exists() else []
        for f in files:
            self.lut.addItem(f.stem, str(f))
        if current and self.lut.findData(current) < 0:
            self.lut.addItem(Path(current).stem + " (" + tr("missing") + ")" if not Path(current).exists()
                             else Path(current).stem, current)
        self.lut.setCurrentIndex(max(0, self.lut.findData(current)))
        self.lut.blockSignals(False)

    def load(self) -> None:
        self._loading = True
        try:
            c = cs.ColorConfig.from_profile(self.ctx.profiles.active()["body"])
            self.enabled.setChecked(c.enabled)
            self.skin.setChecked(c.skin_protect)
            for name, s in self.sliders.items():
                s.setValue(self._to_slider(name, getattr(c, name)))
                self.values[name].setText(f"{getattr(c, name):+.2f}")
            self._fill_luts(c.lut)
            self.preset.clear()
            for n in sorted((self.ctx.settings.get("color_presets") or {}).keys()):
                self.preset.addItem(n, n)
        finally:
            self._loading = False

    def current(self) -> dict:
        d = {name: self._from_slider(name, s.value()) for name, s in self.sliders.items()}
        d.update(enabled=self.enabled.isChecked(), skin_protect=self.skin.isChecked(), lut=self.lut.currentData() or "")
        return d

    def save(self, *_):
        if self._loading:
            return
        d = self.current()
        for name in self.sliders:
            self.values[name].setText(f"{d[name]:+.2f}")
        self.ctx.update_active_profile(lambda b: b.__setitem__("color", d))

    def reset(self) -> None:
        enabled = self.enabled.isChecked()
        self.ctx.update_active_profile(lambda b: b.__setitem__("color", {"enabled": enabled}))
        self.load()

    def import_lut(self, path: str | None = None) -> Path | None:
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self.widget, tr("Choose a LUT"), "", "Cube LUT (*.cube)")
            if not path:
                return None
        try:
            dest = cs.import_lut(Path(path), self.luts_dir)
        except (cs.LutError, OSError, ValueError) as e:
            self.status.setText(tr("Cannot use this LUT: {e}", e=e))
            return None
        self._fill_luts(str(dest))
        self.save()
        self.status.setText(tr("LUT added: {name}", name=dest.stem))
        return dest

    def save_preset(self, name: str | None = None) -> None:
        name = (name if name is not None else self.preset_name.text()).strip()
        if not name:
            self.status.setText(tr("Give the preset a name."))
            return
        presets = dict(self.ctx.settings.get("color_presets") or {})
        presets[name] = {k: v for k, v in self.current().items() if k != "enabled"}
        self.ctx.settings.set("color_presets", presets)
        self.load()

    def apply_preset(self, name: str | None = None) -> None:
        name = name or self.preset.currentData()
        p = (self.ctx.settings.get("color_presets") or {}).get(name)
        if not p:
            return
        d = {**p, "enabled": True}
        self.ctx.update_active_profile(lambda b: b.__setitem__("color", d))
        self.load()

    def tick(self) -> None:
        g = self.ctx.pipeline.grader
        self.status.setText(tr("Grading: {ms} ms per frame", ms=f"{g.ms:.1f}")
                            + (" · " + tr("reduced: sharpness, glow, grain and skin protection skipped to keep the "
                                          "frame rate") if g.reduced else "")
                            + (f" · {g.error}" if g.error else ""))
