"""Virtual studio lights card (Colour Studio tab): key / fill / rim of the active scene's "lighting" section
(vcstudio/replacement/virtual_light.py)."""
from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout, QLabel

from vcstudio.i18n import tr
from vcstudio.replacement.lighting import LightingConfig
from vcstudio.replacement.virtual_light import PRESETS
from vcstudio.ui.studio.common import muted
from vcstudio.ui.widgets import Card

# (key, label, lo, hi, step)
NUMS = [("vl_key", "Key light", 0, 1, 0.05), ("vl_key_angle", "Key direction, ° (− left, + right)", -90, 90, 5),
        ("vl_key_temp", "Key colour, K", 2700, 7500, 100), ("vl_fill", "Fill light", 0, 1, 0.05),
        ("vl_rim", "Rim light", 0, 1, 0.05), ("vl_rim_temp", "Rim colour, K", 2700, 7500, 100),
        ("vl_softness", "Softness", 0, 1, 0.05)]
PRESET_LABELS = {"": "Custom", "studio": "Studio", "soft": "Soft", "dramatic": "Dramatic", "warm": "Warm",
                 "cool": "Cool"}


class LightsCard:
    def __init__(self, ctx):
        self.ctx = ctx
        self.card = Card("Virtual studio lights")
        self.card.layout_.addWidget(muted("Key, fill and rim light painted onto the person, aimed at the largest face "
                                          "(needs face detection or AI Vision). With background segmentation on, "
                                          "only the person is lit, never the background. 2-D relighting: it "
                                          "brightens and tints, it does not cast new shadows."))
        self.enabled = QCheckBox(tr("Use virtual lights in this scene"))
        self.card.layout_.addWidget(self.enabled)
        g = QGridLayout()
        self.preset = QComboBox()
        for k, lbl in PRESET_LABELS.items():
            self.preset.addItem(tr(lbl), k)
        g.addWidget(QLabel(tr("Preset")), 0, 0)
        g.addWidget(self.preset, 0, 1)
        self.nums: dict[str, QDoubleSpinBox] = {}
        for i, (k, lbl, lo, hi, step) in enumerate(NUMS, start=1):
            sp = QDoubleSpinBox()
            sp.setRange(lo, hi)
            sp.setSingleStep(step)
            sp.setDecimals(0 if step >= 5 else 2)
            self.nums[k] = sp
            g.addWidget(QLabel(tr(lbl)), i // 2, (i % 2) * 2)
            g.addWidget(sp, i // 2, (i % 2) * 2 + 1)
        self.card.layout_.addLayout(g)
        self.status = QLabel("—")
        self.status.setObjectName("muted")
        self.card.layout_.addWidget(self.status)
        self._loading = False
        self.load()
        self.enabled.toggled.connect(self.save)
        for sp in self.nums.values():
            sp.valueChanged.connect(self.save)
        self.preset.currentIndexChanged.connect(self.apply_preset)
        ctx.profiles.add_listener(lambda ev, _n: self.load() if ev == "switched" else None)

    def load(self) -> None:
        self._loading = True
        try:
            c = LightingConfig.from_profile(self.ctx.profiles.active()["body"])
            self.enabled.setChecked(c.vl_enabled)
            for k, sp in self.nums.items():
                sp.setValue(float(getattr(c, k)))
            vals = {k: float(getattr(c, k)) for k in self.nums}
            name = next((n for n, p in PRESETS.items() if all(abs(vals[k] - v) < 1e-6 for k, v in p.items())), "")
            self.preset.setCurrentIndex(max(0, self.preset.findData(name)))
        finally:
            self._loading = False

    def save(self, *_) -> None:
        if self._loading:
            return
        d = {"vl_enabled": self.enabled.isChecked(), **{k: round(sp.value(), 2) for k, sp in self.nums.items()}}
        self.ctx.update_active_profile(lambda b: b.setdefault("lighting", {}).update(d))
        self.load()

    def apply_preset(self, *_) -> None:
        name = self.preset.currentData()
        if self._loading or not name:
            return
        d = {**PRESETS[name], "vl_enabled": True}
        self.ctx.update_active_profile(lambda b: b.setdefault("lighting", {}).update(d))
        self.load()

    def tick(self) -> None:
        p = self.ctx.pipeline
        lc = p.scene.lighting if p.scene is not None else None
        on = lc is not None and lc.vl_enabled
        self.status.setText("" if not on else tr("Lights: on the face") if p.relit else
                            tr("Lights: waiting for a face (turn on face detection or AI Vision)"))
