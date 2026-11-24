"""AI Vision tab: face landmarks + head pose, body pose and hands (vcstudio/ai/vision.py), and the pose download."""
from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

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
        self.mesh = QCheckBox(tr("Dense face mesh (478 points, up to 2 faces)"))
        for w in (self.enabled, self.face, self.mesh, self.pose, self.hands, self.overlay):
            c.layout_.addWidget(w)
        g = Card("Gaze and eye contact")
        g.layout_.addWidget(muted("Where the eyes look, from the irises of the face mesh and the head direction. Eye "
                                  "contact correction moves the irises towards the camera inside the eye opening; it "
                                  "stays off while blinking, with the head turned or when the person clearly looks "
                                  "elsewhere. Needs the face mesh model."))
        self.gaze = QCheckBox(tr("Gaze direction"))
        self.eye_contact = QCheckBox(tr("Eye contact correction"))
        g.layout_.addWidget(self.gaze)
        g.layout_.addWidget(self.eye_contact)
        rg = QHBoxLayout()
        rg.addWidget(QLabel(tr("Correction strength, %")))
        self.eye_strength = QSpinBox()
        self.eye_strength.setRange(0, 100)
        self.eye_strength.setSingleStep(5)
        rg.addWidget(self.eye_strength)
        self.calibrate_btn = QPushButton(tr("Calibrate: I am looking at the camera"))
        self.calibrate_btn.clicked.connect(self.calibrate)
        self.reset_cal_btn = QPushButton(tr("Reset calibration"))
        self.reset_cal_btn.clicked.connect(self.reset_calibration)
        rg.addWidget(self.calibrate_btn)
        rg.addWidget(self.reset_cal_btn)
        rg.addStretch(1)
        g.layout_.addLayout(rg)
        self.gaze_status = QLabel("—")
        self.gaze_status.setObjectName("muted")
        self.gaze_status.setWordWrap(True)
        g.layout_.addWidget(self.gaze_status)
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
        lay.addWidget(g)
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
        pc = Card("People in view")
        pc.layout_.addWidget(muted("Tracked people (object detection must be on). A locked person keeps their number "
                                   "through short occlusions (up to 10 s) and is found again by position; "
                                   "auto-framing can follow only the locked people."))
        self.people = QTableWidget(0, 4)
        self.people.setHorizontalHeaderLabels([tr(h) for h in ("Id", "Object", "Confidence", "Locked")])
        self.people.horizontalHeader().setStretchLastSection(True)
        self.people.verticalHeader().setVisible(False)
        self.people.setEditTriggers(QTableWidget.NoEditTriggers)
        self.people.setMinimumHeight(110)
        pc.layout_.addWidget(self.people)
        r3 = QHBoxLayout()
        self.lock_btn = QPushButton(tr("Lock"))
        self.unlock_btn = QPushButton(tr("Unlock"))
        self.locked_only = QCheckBox(tr("Auto-framing follows only locked people"))
        for w in (self.lock_btn, self.unlock_btn, self.locked_only):
            r3.addWidget(w)
        r3.addStretch(1)
        pc.layout_.addLayout(r3)
        lay.addWidget(pc)
        lay.addStretch(1)
        self.lock_btn.clicked.connect(lambda: self.set_locked(True))
        self.unlock_btn.clicked.connect(lambda: self.set_locked(False))
        self.locked_only.toggled.connect(self._save_locked_only)
        ctx.profiles.add_listener(lambda ev, _n: self._load_locked_only() if ev == "switched" else None)
        self.worker = Worker()
        self.worker.done.connect(self._done)
        self.load()
        for w in (self.enabled, self.face, self.mesh, self.pose, self.hands, self.overlay, self.gaze,
                  self.eye_contact):
            w.toggled.connect(self.save)
        self.fps.valueChanged.connect(self.save)
        self.eye_strength.valueChanged.connect(self.save)

    def load(self) -> None:
        v = self.ctx.settings.get("vision")
        self._loading = True
        self.enabled.setChecked(bool(v.get("enabled")))
        self.face.setChecked(bool(v.get("face", True)))
        self.pose.setChecked(bool(v.get("pose", True)))
        self.hands.setChecked(bool(v.get("hands", False)))
        self.overlay.setChecked(bool(v.get("overlay", True)))
        self.mesh.setChecked(bool(v.get("mesh", False)))
        self.gaze.setChecked(bool(v.get("gaze", False)))
        self.eye_contact.setChecked(bool(v.get("eye_contact", False)))
        self.eye_strength.setValue(int(round(float(v.get("eye_contact_strength", 0.7)) * 100)))
        self.fps.setValue(int(v.get("fps", 6)))
        self._loading = False
        self._load_locked_only()
        self._models_text()

    def save(self, *_):
        if self._loading:
            return
        d = dict(self.ctx.settings.get("vision") or {})        # keeps the gaze calibration
        d.update({"enabled": self.enabled.isChecked(), "fps": self.fps.value(),
                  "face": self.face.isChecked(), "pose": self.pose.isChecked(),
                  "hands": self.hands.isChecked(), "overlay": self.overlay.isChecked(),
                  "mesh": self.mesh.isChecked(), "gaze": self.gaze.isChecked(),
                  "eye_contact": self.eye_contact.isChecked(),
                  "eye_contact_strength": round(self.eye_strength.value() / 100, 2)})
        self.ctx.settings.set("vision", d)

    def calibrate(self) -> dict | None:
        cal = self.ctx.vision.calibrate()
        self.gaze_status.setText(tr("Calibrated: yaw {y}°, pitch {p}°", y=cal["yaw"], p=cal["pitch"]) if cal else
                                 tr("No confident gaze right now: turn gaze on and look at the camera."))
        return cal

    def reset_calibration(self) -> None:
        d = dict(self.ctx.settings.get("vision") or {})
        d["gaze_calibration"] = {}
        self.ctx.settings.set("vision", d)
        self.gaze_status.setText(tr("Calibration reset."))

    def _models_text(self) -> None:
        from vcstudio.ai import face_mesh, hands, pose
        from vcstudio.ai.model_download import network_allowed
        lines = [tr("Face: built in (YuNet)."),
                 tr("Face mesh: downloaded.") if face_mesh.available() else tr("Face mesh: not downloaded."),
                 tr("Body pose: downloaded.") if pose.available() else tr("Body pose: not downloaded."),
                 tr("Hands: downloaded.") if hands.available() else
                 tr("Hands: not downloaded (Settings → Gestures → Download).")]
        self.models.setText("\n".join(lines))
        self._missing = [t for t, ok in (("POSE", pose.available()), ("FACE_MESH", face_mesh.available())) if not ok]
        mb = {"POSE": 17, "FACE_MESH": 5}
        self.dl_btn.setText(tr("Download missing models ({mb} MB)", mb=sum(mb[t] for t in self._missing)))
        self.dl_btn.setVisible(bool(self._missing) and network_allowed(self.ctx.settings))

    def download(self) -> None:
        from vcstudio.ai import model_download as md
        from vcstudio.ai.open_vocab import user_models_dir
        self.dl_btn.setEnabled(False)
        self.models.setText(tr("Downloading…"))
        tables = [getattr(md, t) for t in self._missing]
        self.worker.run("download", lambda: [md.download(user_models_dir(), table=t) for t in tables])

    def _done(self, kind, res, err) -> None:
        self.dl_btn.setEnabled(True)
        if err is not None:
            self.models.setText(tr("Download failed: {e}", e=err))
            return
        self._models_text()
        self.ctx.vision.stop()
        self.ctx.vision.apply_settings()

    def _load_locked_only(self) -> None:
        self.locked_only.blockSignals(True)
        self.locked_only.setChecked(bool((self.ctx.profiles.active()["body"].get("framing") or {}).get("locked_only")))
        self.locked_only.blockSignals(False)

    def _save_locked_only(self, on: bool) -> None:
        self.ctx.update_active_profile(lambda b: b.setdefault("framing", {}).__setitem__("locked_only", bool(on)))

    def refresh_people(self, objs=None) -> None:
        if objs is None:
            from vcstudio.core.frame import host_time_s
            objs = self.ctx.ai.tracked_at(host_time_s()) if self.ctx.ai.running else []
        cur = self._selected_id()
        self.people.setRowCount(len(objs))
        for row, o in enumerate(objs):
            vals = (o.track_id, o.label, f"{o.confidence:.2f}", tr("yes") if getattr(o, "locked", False) else "")
            for col, v in enumerate(vals):
                self.people.setItem(row, col, QTableWidgetItem(str(v)))
            if o.track_id == cur:
                self.people.selectRow(row)

    def _selected_id(self) -> int | None:
        r = self.people.currentRow()
        it = self.people.item(r, 0) if r >= 0 else None
        try:
            return int(it.text()) if it else None
        except ValueError:
            return None

    def set_locked(self, on: bool, track_id: int | None = None) -> bool:
        tid = track_id if track_id is not None else self._selected_id()
        if tid is None:
            return False
        tr_ = self.ctx.ai.tracker
        if on:
            return tr_.lock(tid)
        tr_.unlock(tid)
        return True

    def tick(self) -> None:
        self.refresh_people()
        vs = self.ctx.vision
        if vs.cfg.enabled and vs.cfg.needs_gaze:
            r, _ = vs.slot.peek()
            gz = [f.gaze for f in (r.faces if r is not None else []) if f.gaze is not None]
            self.gaze_status.setText("\n".join(
                tr("Face {n}: {dir} · off camera {y}° / {p}° · confidence {c} · stability {s}", n=i + 1,
                   dir=tr(g.direction.replace("_", " ")), y=g.off_yaw, p=g.off_pitch, c=f"{g.confidence:.2f}",
                   s=f"{g.stability:.2f}") for i, g in enumerate(gz)) or tr("No eyes found."))
            if vs.cfg.eye_contact:
                self.gaze_status.setText(self.gaze_status.text() + "\n" + tr(
                    "Eye contact: {n} eyes corrected · frames skipped (mesh not fresh) {k}", n=vs.eyes_corrected,
                    k=vs.eye_skipped))
        s = self.ctx.vision.stats
        parts = ", ".join(f"{k} {v} ms" for k, v in s.parts_ms.items())
        self.status.setText(tr("State: {st} · {fps} analyses/s · faces {f} · poses {p} · hands {h}",
                               st=tr(s.state.replace("_", " ")), fps=f"{s.fps:.1f}", f=s.faces, p=s.poses, h=s.hands)
                            + (f"\n{parts}" if parts else "")
                            + (("\n" + tr("Models missing: {m}", m=", ".join(s.missing))) if s.missing else "")
                            + (f"\n{s.error}" if s.error else ""))
