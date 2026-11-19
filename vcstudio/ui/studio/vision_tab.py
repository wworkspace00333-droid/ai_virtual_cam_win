"""AI Vision tab: face landmarks + head pose, body pose and hands (vcstudio/ai/vision.py), and the pose download."""
from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget

from vcstudio.i18n import tr
from vcstudio.ui.studio.common import Worker, muted
from vcstudio.ui.widgets import Card


class VisionTab:
    def __init__(self, ctx):
        self.ctx = ctx
        self.widget = QWidget()
        lay = QVBoxLayout(self.widget)
        c = Card("AI Vision")
        c.layout_.addWidget(muted("Face points and head direction, body pose and hand skeletons, analysed on the "
                                  "camera picture a few times per second. It is the first thing slowed down when the "
                                  "PC is busy; the camera keeps its frame rate. Nothing here recognises who a person "
                                  "is."))
        self.enabled = QCheckBox(tr("Analyse the picture"))
        self.face = QCheckBox(tr("Face points and head direction"))
        self.pose = QCheckBox(tr("Body pose (skeleton)"))
        self.hands = QCheckBox(tr("Hands (21 points per hand)"))
        self.overlay = QCheckBox(tr("Draw on the camera picture"))
        for w in (self.enabled, self.face, self.pose, self.hands, self.overlay):
            c.layout_.addWidget(w)
        r = QHBoxLayout()
        r.addWidget(QLabel(tr("Analyses per second")))
        self.fps = QSpinBox()
        self.fps.setRange(1, 30)
        r.addWidget(self.fps)
        r.addStretch(1)
        c.layout_.addLayout(r)
        self.status = QLabel("—")
        self.status.setWordWrap(True)
        c.layout_.addWidget(self.status)
        lay.addWidget(c)
        dl = Card("Models")
        self.models = QLabel("—")
        self.models.setWordWrap(True)
        dl.layout_.addWidget(self.models)
        r2 = QHBoxLayout()
        self.dl_btn = QPushButton(tr("Download body pose models (17 MB)"))
        self.dl_btn.clicked.connect(self.download)
        r2.addWidget(self.dl_btn)
        r2.addStretch(1)
        dl.layout_.addLayout(r2)
        lay.addWidget(dl)
        lay.addStretch(1)
        self.worker = Worker()
        self.worker.done.connect(self._done)
        self.load()
        for w in (self.enabled, self.face, self.pose, self.hands, self.overlay):
            w.toggled.connect(self.save)
        self.fps.valueChanged.connect(self.save)

    def load(self) -> None:
        v = self.ctx.settings.get("vision")
        self._loading = True
        self.enabled.setChecked(bool(v.get("enabled")))
        self.face.setChecked(bool(v.get("face", True)))
        self.pose.setChecked(bool(v.get("pose", True)))
        self.hands.setChecked(bool(v.get("hands", False)))
        self.overlay.setChecked(bool(v.get("overlay", True)))
        self.fps.setValue(int(v.get("fps", 6)))
        self._loading = False
        self._models_text()

    def save(self, *_):
        if self._loading:
            return
        self.ctx.settings.set("vision", {"enabled": self.enabled.isChecked(), "fps": self.fps.value(),
                                         "face": self.face.isChecked(), "pose": self.pose.isChecked(),
                                         "hands": self.hands.isChecked(), "overlay": self.overlay.isChecked()})

    def _models_text(self) -> None:
        from vcstudio.ai import hands, pose
        from vcstudio.ai.model_download import network_allowed
        lines = [tr("Face: built in (YuNet)."),
                 tr("Body pose: downloaded.") if pose.available() else tr("Body pose: not downloaded."),
                 tr("Hands: downloaded.") if hands.available() else
                 tr("Hands: not downloaded (Settings → Gestures → Download).")]
        self.models.setText("\n".join(lines))
        self.dl_btn.setVisible(not pose.available() and network_allowed(self.ctx.settings))

    def download(self) -> None:
        from vcstudio.ai import model_download as md
        from vcstudio.ai.open_vocab import user_models_dir
        self.dl_btn.setEnabled(False)
        self.models.setText(tr("Downloading…"))
        self.worker.run("download", lambda: md.download(user_models_dir(), table=md.POSE))

    def _done(self, kind, res, err) -> None:
        self.dl_btn.setEnabled(True)
        if err is not None:
            self.models.setText(tr("Download failed: {e}", e=err))
            return
        self._models_text()
        self.ctx.vision.stop()
        self.ctx.vision.apply_settings()

    def tick(self) -> None:
        s = self.ctx.vision.stats
        parts = ", ".join(f"{k} {v} ms" for k, v in s.parts_ms.items())
        self.status.setText(tr("State: {st} · {fps} analyses/s · faces {f} · poses {p} · hands {h}",
                               st=tr(s.state.replace("_", " ")), fps=f"{s.fps:.1f}", f=s.faces, p=s.poses, h=s.hands)
                            + (f"\n{parts}" if parts else "")
                            + (("\n" + tr("Models missing: {m}", m=", ".join(s.missing))) if s.missing else "")
                            + (f"\n{s.error}" if s.error else ""))
