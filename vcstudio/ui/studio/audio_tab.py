"""Audio Studio tab: microphone, processing chain (vcstudio/core/audio_fx.py) and live level meters."""
from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel, QProgressBar,
                               QPushButton, QVBoxLayout, QWidget)

from vcstudio.core.audio_fx import AudioFxConfig
from vcstudio.i18n import tr
from vcstudio.ui.studio.common import muted
from vcstudio.ui.widgets import Card

# (settings key, label, lo, hi, step) of the numeric controls
NUMS = [("gain_db", "Input gain, dB", -30, 30, 1), ("denoise_strength", "Noise suppression strength", 0, 1, 0.1),
        ("gate_threshold_db", "Gate threshold, dBFS", -90, 0, 1), ("eq_low_db", "Bass (120 Hz), dB", -15, 15, 1),
        ("eq_mid_db", "Mids (1 kHz), dB", -15, 15, 1), ("eq_high_db", "Treble (6 kHz), dB", -15, 15, 1),
        ("comp_threshold_db", "Compressor threshold, dBFS", -60, 0, 1), ("comp_ratio", "Compressor ratio", 1, 20, 0.5),
        ("comp_makeup_db", "Make-up gain, dB", 0, 24, 1), ("agc_target_db", "Automatic gain target, dBFS", -40, -6, 1),
        ("limiter_ceiling_db", "Limiter ceiling, dBFS", -20, 0, 0.5),
        ("deess_threshold_db", "De-esser threshold, dBFS", -60, 0, 1), ("deess_freq", "De-esser from, Hz", 3000, 10000, 250)]
SWITCHES = [("mute", "Mute"), ("denoise", "Noise suppression"), ("gate", "Noise gate"), ("eq", "Equaliser"),
            ("comp", "Compressor"), ("agc", "Automatic gain"), ("deess", "De-esser"), ("limiter", "Limiter")]
PRESET_LABELS = {"": "Custom", "clean": "Clean (limiter only)", "voice": "Voice", "podcast": "Podcast",
                 "noisy_room": "Noisy room"}


class AudioTab:
    def __init__(self, ctx):
        self.ctx = ctx
        self.widget = QWidget()
        lay = QVBoxLayout(self.widget)
        mic = Card("Microphone")
        mic.layout_.addWidget(muted("Sound for recordings and instant replay. The virtual camera itself carries no "
                                    "sound: Windows cameras are video only, and a system-wide virtual microphone "
                                    "needs a signed audio driver (not part of this version)."))
        r = QHBoxLayout()
        self.device = QComboBox()
        self.refresh_btn = QPushButton(tr("Refresh"))
        r.addWidget(self.device, 1)
        r.addWidget(self.refresh_btn)
        mic.layout_.addLayout(r)
        self.live = QCheckBox(tr("Show live levels (uses the microphone while on)"))
        mic.layout_.addWidget(self.live)
        self.monitor = QCheckBox(tr("Hear the processed microphone (use headphones: speakers feed back)"))
        mic.layout_.addWidget(self.monitor)
        g = QGridLayout()
        self.bars = {}
        for i, (k, lbl) in enumerate((("in", "Input"), ("out", "Output"))):
            bar = QProgressBar()
            bar.setRange(-60, 0)
            bar.setFormat("%v dBFS")
            self.bars[k] = bar
            g.addWidget(QLabel(tr(lbl)), i, 0)
            g.addWidget(bar, i, 1)
        mic.layout_.addLayout(g)
        self.meter_text = QLabel("—")
        self.meter_text.setObjectName("muted")
        mic.layout_.addWidget(self.meter_text)
        lay.addWidget(mic)
        ch = Card("Processing")
        pr = QHBoxLayout()
        pr.addWidget(QLabel(tr("Preset")))
        self.preset = QComboBox()
        for k, lbl in PRESET_LABELS.items():
            self.preset.addItem(tr(lbl), k)
        pr.addWidget(self.preset)
        pr.addStretch(1)
        ch.layout_.addLayout(pr)
        sw = QGridLayout()
        self.switches = {}
        for i, (k, lbl) in enumerate(SWITCHES):
            cb = QCheckBox(tr(lbl))
            self.switches[k] = cb
            sw.addWidget(cb, i // 4, i % 4)
        ch.layout_.addLayout(sw)
        ng = QGridLayout()
        self.nums = {}
        for i, (k, lbl, lo, hi, step) in enumerate(NUMS):
            sp = QDoubleSpinBox()
            sp.setRange(lo, hi)
            sp.setSingleStep(step)
            self.nums[k] = sp
            ng.addWidget(QLabel(tr(lbl)), i // 2, (i % 2) * 2)
            ng.addWidget(sp, i // 2, (i % 2) * 2 + 1)
        ch.layout_.addLayout(ng)
        lay.addWidget(ch)
        lay.addStretch(1)
        self._loading = False
        self.refresh_devices()
        self.load()
        self.device.currentIndexChanged.connect(self._device_changed)
        self.refresh_btn.clicked.connect(self.refresh_devices)
        self.live.toggled.connect(lambda on: ctx.recording.set_metering(on))
        for cb in self.switches.values():
            cb.toggled.connect(self.save)
        self.monitor.toggled.connect(self.save)
        for sp in self.nums.values():
            sp.valueChanged.connect(self.save)
        self.preset.currentIndexChanged.connect(self.apply_preset)

    def apply_preset(self, *_) -> None:
        """Set the chain to the chosen preset's values ("Custom": nothing changes)."""
        from vcstudio.core.audio_fx import PRESETS
        if self._loading or not self.preset.currentData():
            return
        d = dict(self.ctx.settings.get("audio") or {})
        d.update(PRESETS[self.preset.currentData()])
        self.ctx.settings.set("audio", d)
        self.load()

    def refresh_devices(self) -> None:
        cur = self.ctx.settings.get("recording.audio_device") or ""
        self.device.blockSignals(True)
        self.device.clear()
        self.device.addItem(tr("No sound"), "")
        try:
            from vcstudio.core.audio import list_microphones
            for name in list_microphones():
                self.device.addItem(name, name)
        except Exception as e:                    # noqa: BLE001 - listing is best effort
            self.meter_text.setText(tr("Cannot list microphones: {e}", e=e))
        if cur and self.device.findData(cur) < 0:
            self.device.addItem(cur + " (" + tr("not connected") + ")", cur)
        self.device.setCurrentIndex(max(0, self.device.findData(cur)))
        self.device.blockSignals(False)

    def _device_changed(self, *_):
        self.ctx.settings.set("recording.audio_device", self.device.currentData() or "")

    def load(self) -> None:
        self._loading = True
        try:
            c = AudioFxConfig.from_settings(self.ctx.settings)
            for k, cb in self.switches.items():
                cb.setChecked(bool(getattr(c, k)))
            for k, sp in self.nums.items():
                sp.setValue(float(getattr(c, k)))
            self.monitor.setChecked(bool((self.ctx.settings.get("audio") or {}).get("monitor", False)))
            from vcstudio.core.audio_fx import preset_name
            self.preset.setCurrentIndex(max(0, self.preset.findData(preset_name(self.ctx.settings.get("audio") or {}))))
        finally:
            self._loading = False

    def save(self, *_):
        if self._loading:
            return
        d = dict(self.ctx.settings.get("audio") or {})
        d.update({k: cb.isChecked() for k, cb in self.switches.items()})
        d.update({k: round(sp.value(), 2) for k, sp in self.nums.items()})
        d["monitor"] = self.monitor.isChecked()
        self.ctx.settings.set("audio", d)
        from vcstudio.core.audio_fx import preset_name
        self._loading = True
        self.preset.setCurrentIndex(max(0, self.preset.findData(preset_name(d))))
        self._loading = False

    def tick(self) -> None:
        m = self.ctx.recording.fx.meter
        self.bars["in"].setValue(int(max(-60, m.in_peak_db)))
        self.bars["out"].setValue(int(max(-60, m.out_peak_db)))
        audio = self.ctx.recording.audio
        state = (tr("microphone off") if audio is None else
                 (audio.error or tr("microphone on: {d}", d=audio.device)))
        mon = self.ctx.recording.monitor
        if mon.error and self.monitor.isChecked():
            state += " · " + tr("monitoring: {e}", e=mon.error)
        self.meter_text.setText(tr("{state} · input RMS {i} dB · output RMS {o} dB · gate {g} · compressor {c} dB · "
                                   "limiter {lim} dB", state=state, i=f"{m.in_rms_db:.0f}", o=f"{m.out_rms_db:.0f}",
                                   g=tr("open") if m.gate_open else tr("closed"), c=f"{m.comp_gain_db:.1f}",
                                   lim=f"{m.limiter_gain_db:.1f}"))
