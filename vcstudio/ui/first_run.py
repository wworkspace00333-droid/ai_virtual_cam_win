"""First-run wizard: language, what this PC has, the video source, the performance mode.

Shown once, before the main window (so the chosen language applies at once): settings ui.first_run_done. Every
step can be skipped and changed later in the app; "Run the setup again" on the Settings page resets the flag.
Nothing here needs administrator rights or the network: camera installation stays on the Virtual Camera page.
"""
from __future__ import annotations


from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QLabel, QProgressBar, QRadioButton, QVBoxLayout,
                               QWizard, QWizardPage)

from vcstudio.i18n import tr


def _label(text: str, muted: bool = False) -> QLabel:
    lb = QLabel(text)
    lb.setWordWrap(True)
    lb.setTextFormat(Qt.RichText)
    if muted:
        lb.setObjectName("muted")
    return lb


def system_summary() -> dict:
    """What the wizard shows on its check page (each probe may fail on its own)."""
    out: dict = {}
    try:
        from vcstudio.platform.win.sysinfo import hardware_gpus, onnx_providers, product_name
        out["windows"] = product_name()
        out["gpus"] = [g["name"] for g in hardware_gpus()]
        out["providers"] = [p.replace("ExecutionProvider", "") for p in onnx_providers()]
    except Exception as e:
        out["error"] = str(e)
    try:
        from vcstudio.camera import camera_manager
        st = camera_manager.status()
        out["camera_installed"], out["camera_visible"] = st.extension_enabled, st.device_visible
    except Exception:
        out["camera_installed"] = out["camera_visible"] = False
    try:
        from vcstudio.core import obs_plugin
        out["obs_version"] = obs_plugin.obs_version()
        out["obs_plugin"] = obs_plugin.installed_plugin() is not None
    except Exception:
        out["obs_version"], out["obs_plugin"] = None, False
    try:
        from vcstudio.core.devices import is_own_output, list_video_devices
        out["cameras"] = [d.name for d in list_video_devices() if not is_own_output(d.name, "")]
    except Exception:
        out["cameras"] = []
    return out


class _LanguagePage(QWizardPage):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.setTitle("Language / Язык")
        lay = QVBoxLayout(self)
        lay.addWidget(_label("Choose the language of the program. / Выберите язык программы."))
        self.combo = QComboBox()
        for key, label in (("auto", "Automatic (Windows display language) / Автоматически (язык Windows)"),
                           ("ru", "Русский"), ("en", "English")):
            self.combo.addItem(label, key)
        self.combo.setCurrentIndex(max(0, self.combo.findData(settings.get("ui.language"))))
        lay.addWidget(self.combo)
        lay.addStretch()

    def validatePage(self) -> bool:
        from vcstudio import i18n
        lang = self.combo.currentData()
        self.settings.set("ui.language", lang, persist=False)
        i18n.set_language(i18n.resolve(lang))
        self.wizard().retranslate()
        return True


class _CheckPage(QWizardPage):
    def __init__(self, info: dict):
        super().__init__()
        self.info = info
        lay = QVBoxLayout(self)
        self.text = _label("")
        lay.addWidget(self.text)
        lay.addStretch()

    def initializePage(self) -> None:
        i = self.info
        self.setTitle(tr("This PC"))
        ok, warn = "✔", "⚠"
        gpus = ", ".join(i.get("gpus") or []) or tr("no graphics card found: AI and effects run on the processor "
                                                   "(slower; Low Resource mode is suggested)")
        rows = [
            f"{ok} {tr('Windows')}: {i.get('windows', '?')}",
            f"{ok if i.get('gpus') else warn} {tr('Graphics card')}: {gpus}",
            f"{ok} {tr('AI runs on')}: {', '.join(i.get('providers') or ['CPU'])}",
            (f"{ok} " + tr("Virtual camera installed")) if i.get("camera_installed") else
            (f"{warn} " + tr("Virtual camera not installed: install it on the Virtual Camera page "
                             "(administrator rights once).")),
            (f"{ok} " + tr("OBS Studio {v}", v=i["obs_version"]) + (" + " + tr("plugin") if i.get("obs_plugin") else
                                                                     " — " + tr("plugin not installed")))
            if i.get("obs_version") else f"· {tr('OBS Studio not found (optional)')}",
            f"{ok if i.get('cameras') else '·'} {tr('Cameras')}: " + (", ".join(i.get("cameras") or []) or tr("none")),
        ]
        self.text.setText("<br>".join(rows))


class _InputPage(QWizardPage):
    def __init__(self, settings, info: dict):
        super().__init__()
        self.settings, self.info = settings, info
        lay = QVBoxLayout(self)
        self.hint = _label("", muted=True)
        lay.addWidget(self.hint)
        self.group = QButtonGroup(self)
        self.obs = QRadioButton(); self.cam = QRadioButton(); self.test = QRadioButton()
        for b in (self.obs, self.cam, self.test):
            self.group.addButton(b); lay.addWidget(b)
        self.cam_combo = QComboBox()
        lay.addWidget(self.cam_combo)
        lay.addStretch()

    def initializePage(self) -> None:
        self.setTitle(tr("Video source"))
        self.hint.setText(tr("Where the program takes the video from. You can change it on the Input page."))
        self.obs.setText(tr("OBS Studio (direct frame transfer through the plugin)"))
        self.cam.setText(tr("A camera:"))
        self.test.setText(tr("Test pattern (to try the program without a camera)"))
        self.cam_combo.clear()
        for name in self.info.get("cameras") or []:
            self.cam_combo.addItem(name, name)
        has_obs = bool(self.info.get("obs_version"))
        self.obs.setEnabled(has_obs)
        self.cam.setEnabled(self.cam_combo.count() > 0)
        self.cam_combo.setEnabled(self.cam_combo.count() > 0)
        (self.obs if has_obs and self.info.get("obs_plugin") else self.cam if self.cam_combo.count() else
         self.test).setChecked(True)

    def validatePage(self) -> bool:
        s = self.settings
        if self.obs.isChecked():
            s.set("input.method", "obs_ipc", persist=False)
        elif self.cam.isChecked() and self.cam_combo.currentData():
            from vcstudio.platform import DEFAULT_DEVICE_METHOD
            s.set("input.method", DEFAULT_DEVICE_METHOD, persist=False)
            s.set("input.device_name_hint", self.cam_combo.currentData(), persist=False)
            s.set("input.device_unique_id", None, persist=False)
        else:
            s.set("input.method", "test_pattern", persist=False)
        return True


class _ModeSig(QObject):
    done = Signal(object)


class _ModePage(QWizardPage):
    """Times the smallest detector here (vcstudio/ai/auto_profile.py) and suggests the mode; the user may pick another."""

    def __init__(self, settings, models):
        super().__init__()
        self.settings, self.models = settings, models
        self.result: dict | None = None
        lay = QVBoxLayout(self)
        self.hint = _label("", muted=True)
        self.bar = QProgressBar(); self.bar.setRange(0, 0)
        self.combo = QComboBox()
        self.note = _label("", muted=True)
        for w in (self.hint, self.bar, self.combo, self.note):
            lay.addWidget(w)
        lay.addStretch()
        self._sig = _ModeSig(); self._sig.done.connect(self._measured)
        self._started = False

    def initializePage(self) -> None:
        from vcstudio.core.config import PERFORMANCE_MODES
        self.setTitle(tr("Performance mode"))
        self.hint.setText(tr("Measuring how fast this PC runs the AI (a few seconds)…"))
        self.combo.clear()
        for k, m in PERFORMANCE_MODES.items():
            self.combo.addItem(tr(m.label), k)
        self.combo.setCurrentIndex(max(0, self.combo.findData(self.settings.get("performance.mode"))))
        if not self._started:
            self._started = True

            def work():
                from vcstudio.ai import auto_profile
                try:
                    r = auto_profile.measure(self.models)
                except Exception as e:
                    r = {"error": str(e)}
                self._sig.done.emit(r)
            from vcstudio.core import maintenance
            maintenance.start("wizard-measure", work)

    def _measured(self, r: dict) -> None:
        self.bar.setRange(0, 1); self.bar.setValue(1)
        self.result = r
        if "error" in r:
            self.hint.setText(tr("Could not measure ({e}); choose a mode yourself.", e=r["error"][:120]))
            return
        self.combo.setCurrentIndex(max(0, self.combo.findData(r["mode"])))
        self.hint.setText(tr("{model} on {backend}: {ms} ms per frame. Suggested mode:", model=r["model"],
                             backend=r["backend"], ms=r["median_ms"]))
        self.note.setText(tr("The camera output stays at 60 fps in every mode; the mode sets how often the AI looks "
                             "at the picture and which model it uses."))

    def validatePage(self) -> bool:
        s = self.settings
        s.set("performance.mode", self.combo.currentData(), persist=False)
        if self.result and "error" not in self.result:
            s.set("performance.auto_mode_done", True, persist=False)
            s.set("performance.auto_mode_note", f"{self.result['model']} on {self.result['backend']}: "
                  f"{self.result['median_ms']} ms per frame (first-run wizard)", persist=False)
        return True


class _DonePage(QWizardPage):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        lay = QVBoxLayout(self)
        self.text = _label("")
        self.autostart = QCheckBox()
        lay.addWidget(self.text); lay.addWidget(self.autostart)
        lay.addStretch()

    def initializePage(self) -> None:
        self.setTitle(tr("Ready"))
        self.text.setText(tr("Next: draw regions on the ROI Editor page and choose replacements on the Replacement "
                             "page. In other apps (Zoom, Teams, Discord, OBS) pick the camera “{cam}”.",
                             cam=self._camera_name()))
        self.autostart.setText(tr("Start capture when the app opens"))
        self.autostart.setChecked(bool(self.settings.get("input.autostart")))

    @staticmethod
    def _camera_name() -> str:
        try:
            from vcstudio.core.devices import own_camera_name
            return own_camera_name()
        except Exception:
            from vcstudio import CAMERA_NAME
            return CAMERA_NAME

    def validatePage(self) -> bool:
        self.settings.set("input.autostart", self.autostart.isChecked(), persist=False)
        return True


class FirstRunWizard(QWizard):
    def __init__(self, settings, models, info: dict | None = None, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("AI Virtual Camera Studio")
        self.setWizardStyle(QWizard.ModernStyle)
        self.setMinimumSize(640, 460)
        info = info if info is not None else system_summary()
        self.pages = [_LanguagePage(settings), _CheckPage(info), _InputPage(settings, info),
                      _ModePage(settings, models), _DonePage(settings)]
        for p in self.pages:
            self.addPage(p)
        self.retranslate()

    def retranslate(self) -> None:
        self.setButtonText(QWizard.NextButton, tr("Next >"))
        self.setButtonText(QWizard.BackButton, tr("< Back"))
        self.setButtonText(QWizard.FinishButton, tr("Finish"))
        self.setButtonText(QWizard.CancelButton, tr("Skip setup"))

    def done(self, result: int) -> None:
        # finished or skipped: do not show again (Settings page: "Run the setup again")
        self.settings.set("ui.first_run_done", True)
        super().done(result)


def run_if_needed(settings, models) -> bool:
    """Show the wizard on the first start; returns True if it was shown."""
    if settings.get("ui.first_run_done"):
        return False
    FirstRunWizard(settings, models).exec()
    return True
