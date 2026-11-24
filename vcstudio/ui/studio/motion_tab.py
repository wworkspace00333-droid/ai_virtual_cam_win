"""AI Camera Motion tab: handheld camera motion of the active scene (vcstudio/replacement/camera_motion.py), its
presets and the user's own (settings "motion_presets"), and a preview of the movement."""
from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QSlider, QSpinBox, QVBoxLayout, QWidget)

from vcstudio.i18n import tr
from vcstudio.replacement import camera_motion as cm
from vcstudio.ui.studio.common import muted
from vcstudio.ui.widgets import Card

LABELS = {"intensity": "Movement intensity", "horizontal": "Horizontal movement", "vertical": "Vertical movement",
          "rotation": "Rotation", "zoom": "Zoom breathing", "speed": "Movement speed", "smoothness": "Smoothness",
          "randomness": "Randomness", "frequency": "Motion frequency", "stabilization": "Motion stabilization"}
STEPS = 100
PREVIEW_W, PREVIEW_H = 320, 180


def to_slider(name: str, v: float) -> int:
    lo, hi = cm.RANGES[name]
    return int(round((v - lo) / (hi - lo) * STEPS))


def from_slider(name: str, pos: int) -> float:
    lo, hi = cm.RANGES[name]
    return round(lo + (hi - lo) * pos / STEPS, 3)


def fmt(name: str, v: float) -> str:
    if name == "speed":
        return f"{v:.2f}×"
    if name == "frequency":
        return f"{v:.1f} Hz"
    return f"{v * 100:.0f} %"


def path_pixmap(cfg: cm.MotionConfig, w: int = PREVIEW_W, h: int = PREVIEW_H) -> QPixmap:
    """The movement of the next 6 seconds with these settings, drawn as the path of the frame centre (at 4x scale:
    the real motion is a few pixels) with the roll shown as a tilted bar at the end."""
    poses = cm.simulate(cfg, 6.0, 30.0, t0=100.0)
    pm = QPixmap(w, h)
    pm.fill(QColor("#111"))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor("#333"), 1))
    p.drawLine(w // 2, 0, w // 2, h)
    p.drawLine(0, h // 2, w, h // 2)
    scale = 4.0
    pts = [QPointF(w / 2 + q.tx * w * scale, h / 2 + q.ty * h * scale) for q in poses]
    for i in range(1, len(pts)):
        a = int(60 + 195 * i / len(pts))
        p.setPen(QPen(QColor(47, 129, 247, a), 1.6))
        p.drawLine(pts[i - 1], pts[i])
    if poses:
        last = poses[-1]
        p.translate(pts[-1])
        p.rotate(-last.rot * scale)
        p.setPen(QPen(QColor("#f0f0f0"), 2))
        p.drawLine(QPointF(-14, 0), QPointF(14, 0))
    p.end()
    return pm


class MotionTab:
    def __init__(self, ctx):
        self.ctx = ctx
        self.widget = QWidget()
        lay = QVBoxLayout(self.widget)
        c = Card("AI Camera Motion")
        c.layout_.addWidget(muted(
            "Makes the camera picture move as if it were filmed by hand: slow drift, a slight tremor of the hands, "
            "inertia and a little roll — never a random shake. The whole picture moves as one, so objects keep "
            "their shape and proportions; a small fixed zoom hides the edges. With object detection on, the "
            "largest object is the centre of the movement and is kept away from the edges. Settings belong to "
            "the active scene."))
        top = QHBoxLayout()
        self.enabled = QCheckBox(tr("Camera motion on"))
        self.mode = QComboBox()
        for k, lbl in cm.MODE_LABELS.items():
            self.mode.addItem(tr(lbl), k)
        self.preset = QComboBox()
        self.preset.addItem(tr("Choose a preset…"), "")
        for k, lbl in cm.PRESET_LABELS.items():
            self.preset.addItem(tr(lbl), k)
        top.addWidget(self.enabled)
        top.addWidget(QLabel(tr("Mode")))
        top.addWidget(self.mode, 1)
        top.addWidget(QLabel(tr("Preset")))
        top.addWidget(self.preset, 1)
        c.layout_.addLayout(top)
        g = QGridLayout()
        self.sliders: dict[str, QSlider] = {}
        self.values: dict[str, QLabel] = {}
        for i, (name, label) in enumerate(LABELS.items()):
            s = QSlider(Qt.Horizontal)
            s.setRange(0, STEPS)
            v = QLabel()
            v.setMinimumWidth(56)
            self.sliders[name], self.values[name] = s, v
            row, col = i // 2, (i % 2) * 3
            g.addWidget(QLabel(tr(label)), row, col)
            g.addWidget(s, row, col + 1)
            g.addWidget(v, row, col + 2)
        c.layout_.addLayout(g)
        r = QHBoxLayout()
        self.seed = QSpinBox()
        self.seed.setRange(0, 99999)
        self.new_seed = QPushButton(tr("New character"))
        self.auto = QCheckBox(tr("Auto intensity"))
        self.auto.setToolTip(tr("Less artificial motion while the picture is already moving"))
        self.object_aware = QCheckBox(tr("Follow the detected object"))
        self.quality = QComboBox()
        for k, lbl in (("auto", "Quality: auto"), ("linear", "Quality: fast"), ("cubic", "Quality: sharp")):
            self.quality.addItem(tr(lbl), k)
        self.reset_btn = QPushButton(tr("Reset"))
        r.addWidget(QLabel(tr("Random seed")))
        r.addWidget(self.seed)
        r.addWidget(self.new_seed)
        r.addWidget(self.auto)
        r.addWidget(self.object_aware)
        r.addWidget(self.quality)
        r.addStretch(1)
        r.addWidget(self.reset_btn)
        c.layout_.addLayout(r)
        lay.addWidget(c)

        pr = Card("My motion presets")
        r2 = QHBoxLayout()
        self.user_preset = QComboBox()
        self.preset_name = QLineEdit()
        self.preset_name.setPlaceholderText(tr("Preset name"))
        self.save_preset_btn = QPushButton(tr("Save as preset"))
        self.apply_preset_btn = QPushButton(tr("Apply preset"))
        self.delete_preset_btn = QPushButton(tr("Delete preset"))
        for w in (self.user_preset, self.apply_preset_btn, self.delete_preset_btn, self.preset_name,
                  self.save_preset_btn):
            r2.addWidget(w)
        r2.setStretch(0, 1)
        r2.setStretch(3, 1)
        pr.layout_.addLayout(r2)
        lay.addWidget(pr)

        pv = Card("Motion preview")
        r3 = QHBoxLayout()
        col1 = QVBoxLayout()
        col1.addWidget(muted("Path of the next 6 seconds (4× enlarged)"))
        self.path = QLabel()
        self.path.setFixedSize(PREVIEW_W, PREVIEW_H)
        col1.addWidget(self.path)
        col2 = QVBoxLayout()
        col2.addWidget(muted("Live output"))
        self.live = QLabel(tr("Start the camera to see the live picture"))
        self.live.setFixedSize(PREVIEW_W, PREVIEW_H)
        self.live.setAlignment(Qt.AlignCenter)
        self.live.setStyleSheet("background: #111; color: #888;")
        col2.addWidget(self.live)
        r3.addLayout(col1)
        r3.addLayout(col2)
        r3.addStretch(1)
        pv.layout_.addLayout(r3)
        self.status = QLabel("—")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        pv.layout_.addWidget(self.status)
        lay.addWidget(pv)
        lay.addStretch(1)

        self._loading = False
        self.load()
        self.enabled.toggled.connect(self.save)
        self.mode.currentIndexChanged.connect(self.save)
        self.auto.toggled.connect(self.save)
        self.object_aware.toggled.connect(self.save)
        self.quality.currentIndexChanged.connect(self.save)
        self.seed.valueChanged.connect(self.save)
        for s in self.sliders.values():
            s.valueChanged.connect(self.save)
        self.preset.currentIndexChanged.connect(self.apply_builtin)
        self.new_seed.clicked.connect(self.next_seed)
        self.reset_btn.clicked.connect(self.reset)
        self.save_preset_btn.clicked.connect(lambda: self.save_preset())
        self.apply_preset_btn.clicked.connect(lambda: self.apply_preset())
        self.delete_preset_btn.clicked.connect(lambda: self.delete_preset())
        # "updated": the hotkey (action motion_toggle) changes the profile without this tab
        ctx.profiles.add_listener(lambda ev, _n: self.load() if ev in ("switched", "updated") else None)

    # ------------------------------------------------------------------ profile <-> controls
    def config(self) -> cm.MotionConfig:
        return cm.MotionConfig.from_profile(self.ctx.profiles.active()["body"])

    def load(self) -> None:
        self._loading = True
        try:
            c = self.config()
            self.enabled.setChecked(c.enabled)
            self.mode.setCurrentIndex(max(0, self.mode.findData(c.mode)))
            for name, s in self.sliders.items():
                s.setValue(to_slider(name, getattr(c, name)))
                self.values[name].setText(fmt(name, getattr(c, name)))
            self.seed.setValue(c.seed)
            self.auto.setChecked(c.auto_intensity)
            self.object_aware.setChecked(c.object_aware)
            self.quality.setCurrentIndex(max(0, self.quality.findData(c.quality)))
            vals = c.preset_values()
            name = next((n for n, p in cm.PRESETS.items()
                         if all(abs(float(vals[k]) - v) < 0.006 if isinstance(v, float) else vals[k] == v
                                for k, v in p.items())), "")
            self.preset.setCurrentIndex(max(0, self.preset.findData(name)))
            self.user_preset.clear()
            for n in sorted((self.ctx.settings.get("motion_presets") or {}).keys()):
                self.user_preset.addItem(n, n)
            self.path.setPixmap(path_pixmap(c))
        finally:
            self._loading = False

    def current(self) -> dict:
        d = {name: from_slider(name, s.value()) for name, s in self.sliders.items()}
        d.update(enabled=self.enabled.isChecked(), mode=self.mode.currentData(), seed=self.seed.value(),
                 auto_intensity=self.auto.isChecked(), object_aware=self.object_aware.isChecked(),
                 quality=self.quality.currentData())
        return cm.MotionConfig.from_dict(d).to_dict()

    def _write(self, d: dict) -> None:
        self.ctx.update_active_profile(lambda b: b.__setitem__("camera_motion", d))
        self.load()

    def save(self, *_) -> None:
        if self._loading:
            return
        self._write(self.current())

    def apply_builtin(self, *_) -> None:
        name = self.preset.currentData()
        if self._loading or not name:
            return
        self._write({**self.current(), **cm.PRESETS[name]})

    def next_seed(self) -> None:
        self.seed.setValue((self.seed.value() * 7 + 13) % 100000)

    def reset(self) -> None:
        self._write({**cm.MotionConfig().to_dict(), "enabled": self.enabled.isChecked()})

    def toggle(self) -> bool:
        self.enabled.setChecked(not self.enabled.isChecked())
        return self.enabled.isChecked()

    # ------------------------------------------------------------------ user presets
    def save_preset(self, name: str | None = None) -> None:
        name = (name if name is not None else self.preset_name.text()).strip()
        if not name:
            self.status.setText(tr("Give the preset a name."))
            return
        presets = dict(self.ctx.settings.get("motion_presets") or {})
        presets[name] = cm.MotionConfig.from_dict(self.current()).preset_values()
        self.ctx.settings.set("motion_presets", presets)
        self.load()
        self.user_preset.setCurrentIndex(max(0, self.user_preset.findData(name)))

    def apply_preset(self, name: str | None = None) -> None:
        name = name or self.user_preset.currentData()
        p = (self.ctx.settings.get("motion_presets") or {}).get(name)
        if not p:
            return
        self._write(cm.MotionConfig.from_dict({**p, "enabled": True}).to_dict())

    def delete_preset(self, name: str | None = None) -> None:
        name = name or self.user_preset.currentData()
        presets = dict(self.ctx.settings.get("motion_presets") or {})
        if presets.pop(name, None) is not None:
            self.ctx.settings.set("motion_presets", presets)
            self.load()

    # ------------------------------------------------------------------ live
    def tick(self) -> None:
        p = self.ctx.pipeline
        mo = p.motion
        st = mo.stats
        pair, _ = p.processed.peek()
        if p.running and pair is not None:
            try:
                th = pair[1].downscaled_copy(PREVIEW_W)
                h, w = th.shape[:2]
                img = QImage(th.data, w, h, w * 4, QImage.Format_ARGB32).copy()
                self.live.setPixmap(QPixmap.fromImage(img).scaled(PREVIEW_W, PREVIEW_H, Qt.KeepAspectRatio,
                                                                  Qt.SmoothTransformation))
            except Exception:
                pass
        if not self.enabled.isChecked() and not mo.active:
            self.status.setText(tr("Camera motion is off."))
            return
        if not p.running:
            self.status.setText(tr("Camera motion is on; start the camera to apply it."))
            return
        parts = [tr("{ms} ms per frame", ms=f"{st.ms:.1f}"), f"{st.backend}", tr("zoom {z}", z=f"{st.scale:.3f}")]
        if self.auto.isChecked():
            parts.append(tr("auto intensity {p} %", p=f"{st.auto_factor * 100:.0f}"))
        if self.object_aware.isChecked():
            parts.append(tr("centred on: {o}", o=st.target) if st.target else tr("no object detected: whole frame"))
        if st.error:
            parts.append(st.error)
        self.status.setText(" · ".join(parts))
