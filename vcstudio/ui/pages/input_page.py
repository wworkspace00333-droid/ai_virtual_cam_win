from __future__ import annotations

import logging

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QTableWidget,
                               QTableWidgetItem, QWidget)

from vcstudio import CAMERA_NAME
from vcstudio.core.config import SettingsError
from vcstudio.core.devices import camera_authorization_status, list_video_devices
from vcstudio.core.video_capture import formats_for_device_id
from vcstudio.ui.video_view import VideoView
from vcstudio.ui.widgets import Card, StatCard, page_scaffold
from vcstudio.i18n import tr
from vcstudio.platform import INPUT_METHODS, is_device_method, text

log = logging.getLogger(__name__)

METHODS = list(INPUT_METHODS)
PERMISSION_HINT = {
    "not_determined": text("camera_permission_not_determined"),
    "denied": text("camera_permission_denied"),
    "restricted": "Restricted by a device-management policy.",
    "authorized": "Granted.",
}


class InputPage:
    key, title = "input", "Input"

    def __init__(self, ctx, feeder):
        self.ctx = ctx
        self.widget, lay = page_scaffold("Input", tr("Choose the video source and capture format. The app never "
                                                     "captures from its own output camera ({camera}).", camera=CAMERA_NAME))
        top = QHBoxLayout()
        src = Card("Source")
        f = QFormLayout()
        self.method = QComboBox()
        for key, label in METHODS:
            self.method.addItem(tr(label), key)
        self.device = QComboBox()
        self.device.setMinimumWidth(320)
        self.format = QComboBox()
        self.fps = QComboBox()
        for v in (60, 30):
            self.fps.addItem(f"{v}" + tr(" FPS"), v)
        self.reconnect = QCheckBox(tr("Reconnect automatically"))
        self.missing = QSpinBox(); self.missing.setRange(200, 60000); self.missing.setSuffix(tr(" ms"))
        self.frozen = QSpinBox(); self.frozen.setRange(200, 60000); self.frozen.setSuffix(tr(" ms"))
        f.addRow(tr("Method"), self.method)
        f.addRow(tr("Device"), self.device)
        # method "video_file": the clip, and whether it starts again at the end
        self.video_row = QWidget(); vr = QHBoxLayout(self.video_row); vr.setContentsMargins(0, 0, 0, 0)
        self.video_path = QLineEdit(); self.video_path.setPlaceholderText(tr("Choose a video file…"))
        self.video_pick = QPushButton(tr("Choose…"))
        self.video_loop = QCheckBox(tr("Loop"))
        vr.addWidget(self.video_path, 1); vr.addWidget(self.video_pick); vr.addWidget(self.video_loop)
        f.addRow(tr("Video file"), self.video_row)
        self.video_info = QLabel(); self.video_info.setObjectName("muted"); self.video_info.setWordWrap(True)
        f.addRow("", self.video_info)
        # playback: start position (saved) and live controls for the running clip
        self.video_ctl = QWidget(); vc = QHBoxLayout(self.video_ctl); vc.setContentsMargins(0, 0, 0, 0)
        self.video_start = QDoubleSpinBox(); self.video_start.setRange(0, 86400); self.video_start.setDecimals(1)
        self.video_start.setSuffix(tr(" s")); self.video_start.setToolTip(tr("Playback and every loop start here."))
        self.video_pause = QPushButton(tr("Pause")); self.video_pause.setCheckable(True)
        self.video_restart = QPushButton(tr("From the start"))
        self.video_pos = QLabel("—"); self.video_pos.setObjectName("muted")
        vc.addWidget(QLabel(tr("Start at"))); vc.addWidget(self.video_start); vc.addWidget(self.video_pause)
        vc.addWidget(self.video_restart); vc.addWidget(self.video_pos, 1)
        f.addRow("", self.video_ctl)
        self.video_pause.toggled.connect(self._video_pause)
        self.video_restart.clicked.connect(self._video_restart)
        self._form = f
        self.video_pick.clicked.connect(self.choose_video)
        self.video_path.editingFinished.connect(self._video_changed)
        f.addRow(tr("Format"), self.format)
        f.addRow(tr("Frame rate"), self.fps)
        f.addRow(tr(""), self.reconnect)
        f.addRow(tr("No-signal timeout"), self.missing)
        f.addRow(tr("Frozen-frame timeout"), self.frozen)
        src.layout_.addLayout(f)
        btns = QHBoxLayout()
        self.refresh_btn = QPushButton(tr("Refresh devices"))
        self.start_btn = QPushButton(tr("Start capture")); self.start_btn.setObjectName("primary")
        self.stop_btn = QPushButton(tr("Stop capture"))
        for b in (self.refresh_btn, self.start_btn, self.stop_btn):
            btns.addWidget(b)
        btns.addStretch()
        src.layout_.addLayout(btns)
        self.perm = QLabel(); self.perm.setObjectName("muted"); self.perm.setWordWrap(True)
        src.layout_.addWidget(self.perm)
        # direct OBS transfer helpers
        self.obs_row = QWidget(); orow = QHBoxLayout(self.obs_row); orow.setContentsMargins(0, 0, 0, 0)
        self.obs_status = QLabel(); self.obs_status.setWordWrap(True)
        self.obs_install = QPushButton(tr("Install OBS plugin"))
        orow.addWidget(self.obs_status, 1); orow.addWidget(self.obs_install)
        src.layout_.addWidget(self.obs_row)
        self.obs_install.clicked.connect(self._install_obs_plugin)
        top.addWidget(src, 3)
        prev = Card("Input monitor")
        self.view = VideoView()
        self.view.setMinimumHeight(240)
        feeder.add_view(self.view, detections=True)
        prev.layout_.addWidget(self.view)
        top.addWidget(prev, 4)
        lay.addLayout(top)

        stats = Card("Input statistics")
        g = QGridLayout(); g.setSpacing(10)
        names = ["State", "Resolution", "Measured FPS", "Latency", "Frames", "Dropped", "Last frame", "Reconnects"]
        self.cards = {n: StatCard(n) for n in names}
        for i, n in enumerate(names):
            g.addWidget(self.cards[n], i // 4, i % 4)
        stats.layout_.addLayout(g)
        self.err = QLabel(); self.err.setWordWrap(True); self.err.setStyleSheet("color: #f85149;")
        stats.layout_.addWidget(self.err)
        lay.addWidget(stats)

        devs = Card("All video devices")
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([tr("Name"), tr("Model"), tr("Usable as input"), tr("Unique ID")])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMinimumHeight(140)
        devs.layout_.addWidget(self.table)
        lay.addWidget(devs)
        lay.addStretch()

        self._load_settings()
        self.refresh_devices()
        self.method.currentIndexChanged.connect(self._method_changed)
        self.device.currentIndexChanged.connect(self._device_changed)
        self.refresh_btn.clicked.connect(self.refresh_devices)
        self.start_btn.clicked.connect(self.start)
        self.stop_btn.clicked.connect(self.ctx.stop_capture)
        self.timer = QTimer(self.widget)
        self.timer.timeout.connect(self.update_stats)
        self.timer.start(250)
        self._method_changed()
        self.ctx.settings.add_listener(self._on_setting)

    def _on_setting(self, key, value):
        # another page (Virtual Camera) may switch the input method; mirror it here
        if key == "input.method" and value != self.method.currentData():
            QTimer.singleShot(0, lambda: self.method.setCurrentIndex(max(0, self.method.findData(value))))

    # ------------------------------------------------------------------
    def _load_settings(self):
        s = self.ctx.settings
        self.method.setCurrentIndex(max(0, self.method.findData(s.get("input.method"))))
        self.fps.setCurrentIndex(max(0, self.fps.findData(s.get("input.preferred_fps"))))
        self.reconnect.setChecked(s.get("input.auto_reconnect"))
        self.missing.setValue(s.get("input.missing_input_timeout_ms"))
        self.frozen.setValue(s.get("input.frozen_frame_timeout_ms"))
        self.video_path.setText(s.get("input.video_path") or "")
        self.video_loop.setChecked(bool(s.get("input.video_loop")))
        self.video_start.setValue(float(s.get("input.video_start_s") or 0.0))
        self._video_changed()

    def _running_video(self):
        from vcstudio.core.video_file_source import VideoFileSource
        src = self.ctx.capture.source
        return src if isinstance(src, VideoFileSource) else None

    def _video_pause(self, on: bool) -> None:
        src = self._running_video()
        if src is not None:
            (src.pause if on else src.resume)()

    def _video_restart(self) -> None:
        src = self._running_video()
        if src is not None:
            src.seek(self.video_start.value())

    def _update_video_controls(self) -> None:
        src = self._running_video()
        for w in (self.video_pause, self.video_restart):
            w.setEnabled(src is not None)
        if src is None:
            self.video_pos.setText("—")
            if self.video_pause.isChecked():
                self.video_pause.blockSignals(True); self.video_pause.setChecked(False); self.video_pause.blockSignals(False)
            return

        def mmss(t: float) -> str:
            return f"{int(t) // 60}:{int(t) % 60:02d}"
        state = tr("paused") if src.paused else (tr("ended") if src.ended else "")
        self.video_pos.setText(f"{mmss(src.position)} / {mmss(src.duration)}" + (f"  ·  {state}" if state else ""))

    # ---------------- video file
    def choose_video(self, path: str | None = None) -> None:
        from pathlib import Path

        from vcstudio.core.video_file_source import VIDEO_FILTER
        if not path:
            start = self.video_path.text() or str(Path.home() / "Videos")
            path, _ = QFileDialog.getOpenFileName(self.widget, tr("Choose a video file"), start,
                                                  tr("Video") + f" ({VIDEO_FILTER})")
        if path:
            self.video_path.setText(path)
            self._video_changed()

    def _video_changed(self) -> None:
        from pathlib import Path
        p = Path(self.video_path.text().strip()) if self.video_path.text().strip() else None
        if p is None:
            self.video_info.setText("")
            return
        from vcstudio.replacement.video import VideoError, probe
        try:
            i = probe(p)
            self.video_info.setText(tr("{w}×{h}, {fps} fps, {s} s, {codec}", w=i.width, h=i.height,
                                       fps=f"{i.fps:.0f}", s=f"{i.duration:.0f}", codec=i.codec))
        except VideoError as e:
            self.video_info.setText(tr(str(e)))

    def _method_changed(self, *_):
        m = self.method.currentData()
        is_dev = is_device_method(m)
        is_file = m == "video_file"
        self._form.setRowVisible(self.video_row, is_file)
        self._form.setRowVisible(self.video_info, is_file)
        self._form.setRowVisible(self.video_ctl, is_file)
        self._form.setRowVisible(self.device, not is_file)
        self._form.setRowVisible(self.format, not is_file)
        if m == "screen" or getattr(self, "_listing_screens", False):
            self.refresh_devices()
        self.device.setEnabled(is_dev or m == "screen")
        self.format.setEnabled(is_dev)
        self.fps.setEnabled(m not in ("obs_ipc", "video_file"))   # OBS / the file decide size and frame rate
        self.obs_row.setVisible(m == "obs_ipc")
        self._refresh_obs_status()

    def _refresh_obs_status(self):
        if self.method.currentData() != "obs_ipc":
            return
        from vcstudio.core import obs_plugin
        from vcstudio.core.obs_shm_source import obs_sharing_status
        st = obs_sharing_status()
        inst = obs_plugin.installed_plugin()
        ver = obs_plugin.obs_version()
        if st["available"]:
            self.obs_status.setText("<span style='color:#3fb950'>●</span> " + tr(
                "OBS is sharing {w}×{h} @ {fps} fps (OBS pid {pid}).", w=st['width'], h=st['height'],
                fps=f"{st['fps']:.0f}", pid=st['writer_pid']))
        elif inst is None:
            self.obs_status.setText(tr("Plugin not installed in OBS") + (f" {ver}" if ver else "") + ". " +
                                    tr("Click Install, restart OBS, then Tools > AI Virtual Camera Studio: share frames."))
        else:
            self.obs_status.setText(tr("Plugin installed.") + f" {tr(st['reason'])}.")
        self.obs_install.setText(tr("Reinstall OBS plugin") if inst else tr("Install OBS plugin"))
        self.obs_install.setEnabled(ver is not None)

    def _install_obs_plugin(self):
        from vcstudio.core import obs_plugin
        try:
            QMessageBox.information(self.widget, tr("OBS plugin"), obs_plugin.install())
        except Exception as e:
            QMessageBox.warning(self.widget, tr("OBS plugin"), tr(str(e)))
        self._refresh_obs_status()

    def _list_screens(self):
        """Method "screen": the device list holds screens and windows (data: the input.screen_target string)."""
        from vcstudio.platform.win import screen_capture as sc
        want = self.ctx.settings.get("input.screen_target") or sc.DEFAULT_TARGET
        self.device.blockSignals(True)
        self.device.clear()
        for m in sc.monitors():
            self.device.addItem(tr("Screen {n} ({w}×{h})", n=m.index + 1, w=m.width, h=m.height), f"monitor:{m.index}")
        for w in sc.windows():
            title = w.title if len(w.title) <= 70 else w.title[:67] + "…"
            self.device.addItem(tr("Window: {title}", title=title), f"window:{w.title}")
        if want.startswith("window:") and self.device.findData(want) < 0:
            self.device.addItem(tr("Window: {title}", title=want[7:]) + " (" + tr("not open now") + ")", want)
        self.device.setCurrentIndex(max(0, self.device.findData(want)))
        self.device.blockSignals(False)
        self.perm.setText(tr("Windows Graphics Capture shows GPU-drawn windows (browsers, games in a window) and a "
                             "window even when others cover it. Where it is unavailable the app falls back to Desktop "
                             "Duplication, then to GDI. Windows may draw a border around the captured area."))

    def refresh_devices(self):
        self._listing_screens = self.method.currentData() == "screen"
        if self._listing_screens:
            self._list_screens()
            return
        devs = list_video_devices()
        want = self.ctx.settings.get("input.device_unique_id")
        hint = self.ctx.settings.get("input.device_name_hint") or ""
        self.device.blockSignals(True)
        self.device.clear()
        sel = -1
        for d in devs:
            label = d.name + ("  (" + tr("this app's output, unusable") + ")" if d.is_own_output else "")
            self.device.addItem(tr(label), d.unique_id)
            if d.is_own_output:
                self.device.model().item(self.device.count() - 1).setEnabled(False)
            elif sel < 0 and (d.unique_id == want or (not want and hint and hint.casefold() in d.name.casefold())):
                sel = self.device.count() - 1
        if sel < 0:
            sel = next((i for i, d in enumerate(devs) if d.is_obs and not d.is_own_output),
                       next((i for i, d in enumerate(devs) if not d.is_own_output), -1))
        self.device.setCurrentIndex(sel)
        self.device.blockSignals(False)
        self._device_changed()
        self.table.setRowCount(len(devs))
        for i, d in enumerate(devs):
            usable = (tr("No: this is {camera} (feedback loop)", camera=CAMERA_NAME) if d.is_own_output
                      else (tr("Yes (OBS)") if d.is_obs else tr("Yes")))
            for j, v in enumerate((d.name, d.model_id, usable, d.unique_id)):
                self.table.setItem(i, j, QTableWidgetItem(v))
        self.table.resizeColumnsToContents()
        st = camera_authorization_status()
        self.perm.setText(tr("Camera permission: {state}.", state=tr(st.replace("_", " "))) + " " + tr(PERMISSION_HINT.get(st, '')))

    def _device_changed(self, *_):
        if self.method.currentData() == "screen":
            return
        uid = self.device.currentData()
        self.format.clear()
        fmts = formats_for_device_id(uid) if uid else []
        seen = set()
        for fm in sorted(fmts, key=lambda x: (-x.width * x.height, -x.max_fps)):
            if (fm.width, fm.height) in seen:
                continue
            seen.add((fm.width, fm.height))
            self.format.addItem(fm.label, (fm.width, fm.height))
        if not fmts:
            for w, h in ((1920, 1080), (1280, 720)):
                self.format.addItem(f"{w}×{h}", (w, h))
        want = (self.ctx.settings.get("input.preferred_width"), self.ctx.settings.get("input.preferred_height"))
        idx = self.format.findData(want)
        self.format.setCurrentIndex(idx if idx >= 0 else 0)

    def apply_to_settings(self) -> bool:
        s = self.ctx.settings
        try:
            s.set("input.method", self.method.currentData(), persist=False)
            if is_device_method(self.method.currentData()):
                s.set("input.device_unique_id", self.device.currentData(), persist=False)
                name = self.device.currentText().split("  (")[0]
                s.set("input.device_name_hint", name, persist=False)
            elif self.method.currentData() == "screen" and self.device.currentData():
                s.set("input.screen_target", self.device.currentData(), persist=False)
            s.set("input.video_path", self.video_path.text().strip(), persist=False)
            s.set("input.video_loop", self.video_loop.isChecked(), persist=False)
            s.set("input.video_start_s", float(self.video_start.value()), persist=False)
            w, h = self.format.currentData() or (1920, 1080)
            s.set("input.preferred_width", w, persist=False)
            s.set("input.preferred_height", h, persist=False)
            s.set("input.preferred_fps", self.fps.currentData(), persist=False)
            s.set("input.auto_reconnect", self.reconnect.isChecked(), persist=False)
            s.set("input.missing_input_timeout_ms", self.missing.value(), persist=False)
            s.set("input.frozen_frame_timeout_ms", self.frozen.value(), persist=False)
            s.save()
            return True
        except SettingsError as e:
            QMessageBox.warning(self.widget, tr("Input"), tr(str(e)))
            return False

    def start(self):
        if is_device_method(self.method.currentData()) and self.device.currentData() is None:
            QMessageBox.warning(self.widget, tr("Input"), tr("No usable video device selected."))
            return
        if self.method.currentData() == "video_file" and not self.video_path.text().strip():
            self.choose_video()
            if not self.video_path.text().strip():
                return
        if self.apply_to_settings():
            try:
                self.ctx.start_capture()
            except RuntimeError as e:
                QMessageBox.warning(self.widget, tr("Input"), tr(str(e)))

    def update_stats(self):
        self._tick_n = getattr(self, "_tick_n", 0) + 1
        if self._tick_n % 8 == 0:
            self._refresh_obs_status()
        if self.method.currentData() == "video_file":
            self._update_video_controls()
        s = self.ctx.capture.stats()
        state_level = {"running": "ok", "frozen": "warn", "reconnecting": "warn", "no_signal": "error",
                       "error": "error"}.get(s.state, "idle")
        self.cards["State"].set(s.state.replace("_", " ").title(), s.source_name or "", state_level)
        live = s.state not in ("stopped", "error")
        if live and s.width:
            note = tr("{fmt} · nominal {fps} fps", fmt=s.pixel_format, fps=f"{s.nominal_fps:g}")
            if s.shared:      # another app controls the webcam: its mode, which may differ from the chosen one
                note += " · " + tr("shared with another app")
            self.cards["Resolution"].set(f"{s.width}×{s.height}", note)
        else:
            self.cards["Resolution"].set("—", "")
        fps_level = "ok" if live and s.measured_fps >= 0.9 * max(1, s.nominal_fps) else ("warn" if live else "idle")
        self.cards["Measured FPS"].set(f"{s.measured_fps:.1f}" if live else "—", "1 s sliding window", fps_level)
        self.cards["Latency"].set(f"{s.latency_ms:.1f} " + tr("ms") if live and s.frames else "—", "frame timestamp → app")
        self.cards["Frames"].set(f"{s.frames:,}", tr("uptime {s} s", s=f"{s.session_uptime_s:.0f}") if live else "")
        self.cards["Dropped"].set(f"{s.dropped_by_source}", tr("late-frame discards; {n} superseded in slot", n=s.replaced_unconsumed))
        age = tr("{ms} ms ago", ms=f"{s.last_frame_age_ms:.0f}") if live and s.frames else "—"
        frozen = tr("identical for {s} s", s=f"{s.frozen_for_s:.1f}") if s.frozen_for_s else ""
        self.cards["Last frame"].set(age, frozen, "warn" if frozen else None)
        self.cards["Reconnects"].set(str(s.reconnects), "")
        self.err.setText(tr(s.last_error) if s.state in ("error", "reconnecting", "no_signal") else "")
        self.start_btn.setEnabled(s.state in ("stopped", "error"))
        self.stop_btn.setEnabled(s.state not in ("stopped",))
