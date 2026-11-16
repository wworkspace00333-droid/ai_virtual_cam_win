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
        lay.addStretch()

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
