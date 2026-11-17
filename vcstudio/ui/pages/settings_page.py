from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QComboBox, QFormLayout, QHBoxLayout, QKeySequenceEdit, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QSpinBox, QCheckBox)

from vcstudio.core.config import SettingsError
from vcstudio.ui.actions import ACTION_SPECS
from vcstudio.ui.widgets import Card, page_scaffold
from vcstudio.i18n import tr
from vcstudio.platform import COMPOSITORS, INPUT_METHODS, text


class SettingsPage:
    key, title = "settings", "Settings"

    def __init__(self, ctx, actions, window=None):
        self.ctx, self.actions, self.window = ctx, actions, window
        self.widget, lay = page_scaffold("Settings", tr("Stored locally in {path}", path=ctx.paths.settings_file))
        s = ctx.settings
        gen = Card("Input defaults")
        f = QFormLayout()
        # The input method is chosen on the Input page only: several methods need more than a name (a device, a file,
        # a screen or window), and a second, partial selector here could pick "Video file" without a file.
        mrow = QHBoxLayout()
        self.method_label = QLabel()
        self.method_btn = QPushButton(tr("Change on the Input page"))
        self.method_btn.clicked.connect(self._goto_input)
        mrow.addWidget(self.method_label, 1); mrow.addWidget(self.method_btn)
        self._show_method(s.get("input.method"))
        ctx.settings.add_listener(lambda k, v: self._show_method(v) if k == "input.method" else None)
        self.reconnect = QCheckBox(tr("Reconnect automatically"))
        self.reconnect.setChecked(s.get("input.auto_reconnect"))
        self.autostart = QCheckBox(tr("Start capture when the app opens"))
        self.autostart.setChecked(s.get("input.autostart"))
        self.frozen = QSpinBox(); self.frozen.setRange(100, 60000); self.frozen.setSuffix(tr(" ms"))
        self.frozen.setValue(s.get("input.frozen_frame_timeout_ms"))
        f.addRow(tr("Input method"), mrow)
        f.addRow(tr(""), self.reconnect)
        f.addRow(tr(""), self.autostart)
        f.addRow(tr("Frozen-frame timeout"), self.frozen)
        gen.layout_.addLayout(f)
        lay.addWidget(gen)

        out = Card("Output defaults")
        f2 = QFormLayout()
        self.res = QComboBox()
        for w, h in ((1920, 1080), (1280, 720)):
            self.res.addItem(f"{w} × {h}", (w, h))
        self.res.setCurrentIndex(0 if s.get("output.width") == 1920 else 1)
        self.fps = QComboBox()
        for v in (60, 30):
            self.fps.addItem(f"{v}" + tr(" FPS"), v)
        self.fps.setCurrentIndex(self.fps.findData(s.get("output.fps")))
        self.pix = QComboBox(); self.pix.addItems(["BGRA", "NV12"]); self.pix.setCurrentText(s.get("output.pixel_format"))
        self.comp = QComboBox()
        for key, label in COMPOSITORS:
            self.comp.addItem(tr(label), key)
        self.comp.setCurrentIndex(max(0, self.comp.findData(s.get("processing.compositor"))))
        self.loglevel = QComboBox(); self.loglevel.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        self.loglevel.setCurrentText(s.get("logging.level"))
        f2.addRow(tr("Resolution"), self.res)
        f2.addRow(tr("Frame rate"), self.fps)
        f2.addRow(tr("Pixel format"), self.pix)
        f2.addRow(tr("Compositor"), self.comp)
        f2.addRow(tr("Log level"), self.loglevel)
        from vcstudio.core.performance import auto_memory_warning_mb, total_memory_mb
        auto_warn = auto_memory_warning_mb()
        self.mem_mode = QComboBox()
        self.mem_mode.addItem(tr(text("memory_auto"), mb=auto_warn, gb=f"{total_memory_mb() / 1024:.0f}"), "auto")
        self.mem_mode.addItem(tr("Manual"), "manual")
        self.mem_mode.setCurrentIndex(max(0, self.mem_mode.findData(s.get("performance.memory_limits"))))
        self.mem_warn = QSpinBox(); self.mem_warn.setRange(256, 262144); self.mem_warn.setSingleStep(256)
        self.mem_warn.setSuffix(tr(" MB")); self.mem_warn.setValue(s.get("performance.memory_warning_mb"))
        self.mem_warn.setEnabled(self.mem_mode.currentData() == "manual")
        self.mem_mode.currentIndexChanged.connect(lambda _: self.mem_warn.setEnabled(self.mem_mode.currentData() == "manual"))
        self.mem_warn.setToolTip(tr("App memory above this logs a warning; above twice this the app sheds load "
                                 "(Low Resource mode, closes video players, clears caches)."))
        from vcstudio.i18n import LANGUAGES
        self.lang = QComboBox()
        self.lang.addItem(tr(text("language_auto")), "auto")
        for code, name in LANGUAGES.items():
            self.lang.addItem(name, code)
        self.lang.setCurrentIndex(max(0, self.lang.findData(s.get("ui.language"))))
        f2.addRow(tr("Language"), self.lang)
        f2.addRow(tr("Memory warning"), self.mem_mode)
        f2.addRow(tr("Manual threshold"), self.mem_warn)
        out.layout_.addLayout(f2)
        lay.addWidget(out)
        save = QPushButton(tr("Save settings")); save.setObjectName("primary")
        save.clicked.connect(self.save)
        lay.addWidget(save)

        hk = Card("Hotkeys")
        note = QLabel(tr(text("hotkeys_note")))
        note.setObjectName("muted"); note.setWordWrap(True)
        hk.layout_.addWidget(note)
        self.global_hk = QCheckBox(tr("System-wide (global) hotkeys"))
        self.global_hk.setChecked(s.get("ui.global_hotkeys"))
        hk.layout_.addWidget(self.global_hk)
        self.hk_status = QLabel(); self.hk_status.setWordWrap(True); self.hk_status.setStyleSheet("color: #d29922;")
        hk.layout_.addWidget(self.hk_status)
        f3 = QFormLayout()
        self.editors: dict[str, QKeySequenceEdit] = {}
        bindings = s.get("hotkeys")
        for spec in ACTION_SPECS:
            e = QKeySequenceEdit(QKeySequence(bindings.get(spec.id, "")))
            for le in e.findChildren(QLineEdit):          # Qt's built-in "Press shortcut" placeholder
                le.setPlaceholderText(tr("Press shortcut"))
            e.setMaximumSequenceLength(1) if hasattr(e, "setMaximumSequenceLength") else None
            label = tr(spec.label)
            f3.addRow(label, e)
            self.editors[spec.id] = e
        hk.layout_.addLayout(f3)
        row = QHBoxLayout()
        apply_btn = QPushButton(tr("Apply hotkeys")); apply_btn.setObjectName("primary")
        reset_btn = QPushButton(tr("Reset to defaults"))
        row.addWidget(apply_btn); row.addWidget(reset_btn); row.addStretch()
        hk.layout_.addLayout(row)
        apply_btn.clicked.connect(self.apply_hotkeys)
        reset_btn.clicked.connect(self.reset_hotkeys)
        lay.addWidget(hk)

        from vcstudio.platform import IS_WINDOWS
        if IS_WINDOWS:          # tray, start with Windows, setup again, diagnostics
            from vcstudio.platform.win import autostart
            app = Card("Windows and tray")
            self.win_start = QCheckBox(tr("Start with Windows (in the tray)"))
            self.win_start.setChecked(autostart.is_enabled())
            self.to_tray = QCheckBox(tr("Closing the window keeps the app running in the tray"))
            self.to_tray.setChecked(bool(s.get("ui.close_to_tray")))
            app.layout_.addWidget(self.win_start); app.layout_.addWidget(self.to_tray)
            brow = QHBoxLayout()
            self.wizard_btn = QPushButton(tr("Run the setup again"))
            self.diag_btn = QPushButton(tr("Save diagnostics…"))
            brow.addWidget(self.wizard_btn); brow.addWidget(self.diag_btn); brow.addStretch()
            app.layout_.addLayout(brow)
            self.app_info = QLabel(); self.app_info.setObjectName("muted"); self.app_info.setWordWrap(True)
            app.layout_.addWidget(self.app_info)
            lay.addWidget(app)
            self.win_start.toggled.connect(self._toggle_autostart)
            self.to_tray.toggled.connect(lambda on: s.set("ui.close_to_tray", on))
            self.wizard_btn.clicked.connect(self._run_wizard)
            self.diag_btn.clicked.connect(self._save_diagnostics)
        lay.addWidget(self._gestures_card())
        lay.addWidget(self._plugins_card())
        lay.addWidget(self._updates_card())
        lay.addStretch()

    # ---- gestures (vcstudio/ai/gestures.py)
    GESTURE_LABELS = {"open_palm": "Open palm", "fist": "Fist", "victory": "Victory (two fingers)",
                      "point": "Pointing finger", "thumbs_up": "Thumbs up"}

    def _gestures_card(self):
        from PySide6.QtCore import QTimer
        from vcstudio.ai.gestures import GestureConfig
        card = Card("Gestures")
        note = QLabel(tr("Hold a gesture in front of the camera for about half a second to run an action. The hand "
                         "is recognised on this PC (MediaPipe hand models, about 8 MB, downloaded once); nothing "
                         "is recorded or sent."))
        note.setObjectName("muted"); note.setWordWrap(True)
        card.layout_.addWidget(note)
        row = QHBoxLayout()
        self.gest_on = QCheckBox(tr("Gesture control"))
        self.gest_dl = QPushButton(tr("Download hand models (8 MB)"))
        row.addWidget(self.gest_on); row.addWidget(self.gest_dl); row.addStretch()
        card.layout_.addLayout(row)
        f = QFormLayout()
        cfg = GestureConfig.from_settings(self.ctx.settings)
        self.gest_combos: dict[str, QComboBox] = {}
        for g, label in self.GESTURE_LABELS.items():
            c = QComboBox()
            c.addItem(tr("— nothing —"), "")
            for spec in ACTION_SPECS:
                if not spec.id.startswith(("scene_", "card_")):
                    c.addItem(tr(spec.label), spec.id)
            c.setCurrentIndex(max(0, c.findData(cfg.actions.get(g, ""))))
            c.currentIndexChanged.connect(self._save_gesture_map)
            f.addRow(tr(label), c)
            self.gest_combos[g] = c
        card.layout_.addLayout(f)
        self.gest_status = QLabel(); self.gest_status.setObjectName("muted"); self.gest_status.setWordWrap(True)
        card.layout_.addWidget(self.gest_status)
        self.gest_on.setChecked(cfg.enabled)
        self.gest_on.toggled.connect(lambda on: self.ctx.settings.set("gestures.enabled", on))
        self.gest_dl.clicked.connect(self.download_hand_models)
        self._gest_dl_thread = None
        self._gest_dl_msg = ""
        self._gest_timer = QTimer(self.widget)
        self._gest_timer.timeout.connect(self.refresh_gestures)
        self._gest_timer.start(1000)
        self.refresh_gestures()
        return card

    def _save_gesture_map(self, *_):
        self.ctx.settings.set("gestures.actions", {g: c.currentData() or "" for g, c in self.gest_combos.items()})

    def refresh_gestures(self) -> None:
        from vcstudio.ai import hands
        from vcstudio.ai import model_download as md
        have = hands.available()
        busy = self._gest_dl_thread is not None and self._gest_dl_thread.is_alive()
        self.gest_dl.setVisible(not have and md.network_allowed(self.ctx.settings))
        self.gest_dl.setEnabled(not busy)
        self.gest_on.setEnabled(have)
        st = self.ctx.gestures.stats
        if busy or self._gest_dl_msg:
            text = self._gest_dl_msg
        elif not have:
            text = tr("The hand models are not installed yet.")
        elif st.state == "running":
            g = tr(self.GESTURE_LABELS.get(st.gesture, "")) if st.gesture else tr("none")
            text = tr("Running ({backend}, {fps} fps, {ms} ms): {hands} hand(s), gesture: {g}",
                      backend=st.backend or "—", fps=f"{st.fps:.0f}", ms=f"{st.ms:.0f}", hands=st.hands, g=g)
            if st.last_action:
                text += " · " + tr("last: {a}", a=st.last_action)
        elif st.state == "error":
            text = tr("Gestures stopped: {e}", e=st.error)
        else:
            text = tr("Off.")
        self.gest_status.setText(text)

    def download_hand_models(self, opener=None) -> None:
        import threading

        from vcstudio.ai import model_download as md
        from vcstudio.ai.open_vocab import user_models_dir
        if self._gest_dl_thread is not None and self._gest_dl_thread.is_alive():
            return
        self._gest_dl_msg = tr("Downloading the hand models…")

        def work():
            try:
                md.download(user_models_dir(), table=md.HANDS, opener=opener)
                self._gest_dl_msg = tr("Hand models installed. Switch on Gesture control.")
                self.ctx.db.log_event("INFO", "gestures", "hand models downloaded")
            except md.DownloadError as e:
                self._gest_dl_msg = tr("Download failed: {e}", e=e)
        self._gest_dl_thread = threading.Thread(target=work, name="hands-download", daemon=True)
        self._gest_dl_thread.start()
        self.refresh_gestures()

    # ---- plugins (vcstudio/plugins): listed without running them, switched on one by one
    def _plugins_card(self):
        from PySide6.QtWidgets import QListWidget
        card = Card("Plugins")
        note = QLabel(tr("Your own Python effects on the camera output (docs/PLUGIN_SDK.md). Put a plugin's .py file "
                         "in the plugins folder and switch it on here. A plugin runs with this app's rights: use only "
                         "plugins you trust. A changed file is switched off until you switch it on again."))
        note.setObjectName("muted"); note.setWordWrap(True)
        card.layout_.addWidget(note)
        self.plugin_list = QListWidget(); self.plugin_list.setMaximumHeight(140)
        card.layout_.addWidget(self.plugin_list)
        row = QHBoxLayout()
        folder_btn = QPushButton(tr("Open plugins folder")); reload_btn = QPushButton(tr("Reload list"))
        row.addWidget(folder_btn); row.addWidget(reload_btn); row.addStretch()
        card.layout_.addLayout(row)
        self.plugin_status = QLabel(); self.plugin_status.setObjectName("muted"); self.plugin_status.setWordWrap(True)
        card.layout_.addWidget(self.plugin_status)
        folder_btn.clicked.connect(self._open_plugins_folder)
        reload_btn.clicked.connect(self.refresh_plugins)
        self.plugin_list.itemChanged.connect(self._plugin_toggled)
        self.confirm_plugin = self._confirm_plugin          # replaceable in tests (a modal box would block)
        self.refresh_plugins()
        return card

    def refresh_plugins(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QListWidgetItem
        pm = self.ctx.plugins
        try:
            infos = pm.discover()
        except OSError as e:
            self.plugin_status.setText(tr("Plugins folder not available: {e}", e=e))
            return
        self.plugin_list.blockSignals(True)
        self.plugin_list.clear()
        for i in infos:
            state = {"running": tr("running"), "off": tr("off"), "error": tr("stopped after an error"),
                     "incompatible": tr("incompatible"), "invalid": tr("invalid")}.get(i.state, i.state)
            extra = f" — {i.error}" if i.error else (tr(" — slow ({ms} ms)", ms=f"{i.avg_ms:.0f}") if i.slow else "")
            it = QListWidgetItem(f"{i.name} ({i.id}.py) · {state}{extra}")
            it.setToolTip(i.description or i.id)
            it.setData(Qt.ItemDataRole.UserRole, i.id)
            flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            if i.state not in ("incompatible", "invalid"):
                flags |= Qt.ItemFlag.ItemIsUserCheckable
            it.setFlags(flags)
            it.setCheckState(Qt.CheckState.Checked if i.state == "running" else Qt.CheckState.Unchecked)
            self.plugin_list.addItem(it)
        self.plugin_list.blockSignals(False)
        self.plugin_status.setText(tr("{n} plugin(s) in {path}", n=len(infos), path=pm.folder) if infos else
                                   tr("No plugins in {path}", path=pm.folder))

    def _confirm_plugin(self, info) -> bool:
        r = QMessageBox.question(self.widget, tr("Plugins"),
                                 tr("Switch on “{name}”?\n\n{path}\n\nIts code will run inside this app with your "
                                    "rights. Only continue if you trust where it comes from.",
                                    name=info.name, path=info.path))
        return r == QMessageBox.StandardButton.Yes

    def _plugin_toggled(self, item) -> None:
        from PySide6.QtCore import Qt
        pm = self.ctx.plugins
        pid = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            info = pm.infos.get(pid)
            if info is not None and self.confirm_plugin(info):
                info = pm.enable(pid)
                if info.state != "running":
                    self.plugin_status.setText(tr("“{name}” did not start: {e}", name=info.name, e=info.error))
        else:
            pm.disable(pid)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self.refresh_plugins)          # not inside the list's own signal

    def _open_plugins_folder(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        self.ctx.plugins.folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.ctx.plugins.folder)))

    # ---- "Check for updates" (vcstudio/app/update_check.py): one request, only on this click
    def _updates_card(self):
        from vcstudio import __version__
        from vcstudio.app import update_check as uc
        card = Card("Updates")
        row = QHBoxLayout()
        self.update_btn = QPushButton(tr("Check for updates"))
        self.update_btn.setToolTip(tr("Asks GitHub once whether a newer release exists. Nothing about you or this "
                                      "computer is sent, nothing is downloaded or installed, and the app never "
                                      "checks by itself."))
        row.addWidget(QLabel(tr("Version {v}", v=__version__))); row.addWidget(self.update_btn); row.addStretch()
        card.layout_.addLayout(row)
        self.update_info = QLabel(); self.update_info.setObjectName("muted"); self.update_info.setWordWrap(True)
        self.update_info.setOpenExternalLinks(True)
        card.layout_.addWidget(self.update_info)
        if not uc.allowed(self.ctx.settings):
            self.update_btn.setEnabled(False)
            self.update_info.setText(tr("Network access is turned off (privacy settings), so the app does not check "
                                        "for updates."))
        self.update_btn.clicked.connect(self.check_updates)
        self._update_result = None
        self._update_thread = None
        return card

    def check_updates(self, opener=None) -> None:
        import threading

        from PySide6.QtCore import QTimer

        from vcstudio import __version__
        from vcstudio.app import update_check as uc
        if not uc.allowed(self.ctx.settings) or (self._update_thread and self._update_thread.is_alive()):
            return
        self.update_btn.setEnabled(False)
        self.update_info.setText(tr("Checking…"))
        self._update_result = None

        def work():
            try:
                self._update_result = uc.check(__version__, opener)
            except uc.UpdateCheckError as e:
                self._update_result = e
        self._update_thread = threading.Thread(target=work, name="update-check", daemon=True)
        self._update_thread.start()

        def poll():
            if self._update_thread is not None and self._update_thread.is_alive():
                QTimer.singleShot(200, poll)
                return
            self.show_update_result(self._update_result)
        QTimer.singleShot(200, poll)

    def show_update_result(self, res) -> None:
        from vcstudio.app.update_check import UpdateInfo
        self.update_btn.setEnabled(True)
        if not isinstance(res, UpdateInfo):
            self.update_info.setText(tr("Could not check for updates: {e}", e=res))
            return
        if res.newer:
            kind = tr(" (preview)") if res.prerelease else ""
            self.update_info.setText(tr("Version {v}{kind} is available: <a href=\"{url}\">release page</a>. "
                                        "Download and install it from there.", v=res.latest, kind=kind, url=res.url))
        elif res.latest:
            self.update_info.setText(tr("You have the latest version ({v}).", v=res.current))
        else:
            self.update_info.setText(tr("No releases found."))
        import logging
        logging.getLogger(__name__).info("Update check: current %s, latest %s", res.current, res.latest or "-")

    def _toggle_autostart(self, on: bool) -> None:
        from vcstudio.platform.win import autostart
        try:
            autostart.set_enabled(on)
            self.app_info.setText(tr("Starts with Windows: {cmd}", cmd=autostart.current()) if on
                                  else tr("Does not start with Windows."))
        except OSError as e:
            self.app_info.setText(tr("Could not change: {e}", e=e))

    def _run_wizard(self) -> None:
        from vcstudio.ui.first_run import FirstRunWizard
        FirstRunWizard(self.ctx.settings, self.ctx.models, parent=self.widget).exec()
        self.app_info.setText(tr("Setup saved. A changed language applies after a restart."))

    def _save_diagnostics(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        from vcstudio.app.diagnostics import default_name, write_zip
        path, _ = QFileDialog.getSaveFileName(self.widget, tr("Save diagnostics"),
                                              str(self.ctx.paths.root / default_name()), "ZIP (*.zip)")
        if not path:
            return
        try:
            info = write_zip(self.ctx, Path(path))
            self.app_info.setText(tr("Saved {path} ({n} files, {kb} KB). Personal data (user name, computer name, "
                                     "home folder) is replaced. Nothing is sent anywhere.",
                                     path=path, n=info["files"], kb=info["kb"]))
        except Exception as e:
            self.app_info.setText(tr("Could not save diagnostics: {e}", e=e))

    def _show_method(self, method) -> None:
        label = dict(INPUT_METHODS).get(method, str(method))
        self.method_label.setText(tr(label))

    def _goto_input(self) -> None:
        if self.window is not None and hasattr(self.window, "goto"):
            self.window.goto("input")

    def save(self):
        s = self.ctx.settings
        try:
            s.set("input.auto_reconnect", self.reconnect.isChecked(), persist=False)
            s.set("input.autostart", self.autostart.isChecked(), persist=False)
            s.set("input.frozen_frame_timeout_ms", self.frozen.value(), persist=False)
            w, h = self.res.currentData()
            s.set("output.width", w, persist=False); s.set("output.height", h, persist=False)
            s.set("output.fps", self.fps.currentData(), persist=False)
            s.set("output.pixel_format", self.pix.currentText(), persist=False)
            s.set("logging.level", self.loglevel.currentText(), persist=False)
            s.set("processing.compositor", self.comp.currentData(), persist=False)
            s.set("performance.memory_warning_mb", self.mem_warn.value(), persist=False)
            s.set("performance.memory_limits", self.mem_mode.currentData(), persist=False)
            lang_changed = s.get("ui.language") != self.lang.currentData()
            s.set("ui.language", self.lang.currentData(), persist=False)
            s.save()
            if lang_changed:
                QMessageBox.information(self.widget, tr("Language"), tr("The new language is used after you restart the app."))
            import logging
            logging.getLogger().setLevel(self.loglevel.currentText())
            logging.getLogger(__name__).info("Settings saved")
        except SettingsError as e:
            QMessageBox.warning(self.widget, tr("Settings"), tr(str(e)))

    def current_bindings(self) -> dict[str, str]:
        return {aid: e.keySequence().toString(QKeySequence.PortableText) for aid, e in self.editors.items()}

    def apply_hotkeys(self):
        b = self.current_bindings()
        seqs = [v for v in b.values() if v]
        dupes = sorted({v for v in seqs if seqs.count(v) > 1})
        if dupes:
            QMessageBox.warning(self.widget, tr("Hotkeys"), tr("The same shortcut is assigned twice: ") + ", ".join(dupes))
            return
        self.ctx.settings.set("hotkeys", b)
        self.ctx.settings.set("ui.global_hotkeys", self.global_hk.isChecked())
        errors = self.window.apply_hotkeys() if self.window else {}
        self.hk_status.setText("\n".join(f"{a}: {e}" for a, e in errors.items()))

    def reset_hotkeys(self):
        self.ctx.settings.reset_hotkeys()
        b = self.ctx.settings.get("hotkeys")
        for aid, e in self.editors.items():
            e.setKeySequence(QKeySequence(b.get(aid, "")))
        if self.window:
            self.window.apply_hotkeys()
