from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QPushButton

from vcstudio.ui.video_view import BLACK_INPUT_HINT, STATE_OVERLAY, VideoView, frame_is_black, frame_to_qimage
from vcstudio.ui.widgets import page_scaffold
from vcstudio.i18n import tr


class PreviewPage:
    """Original | processed, both taken from the same pipeline pair, so they show the same moment."""
    key, title = "preview", "Preview"

    def __init__(self, ctx, feeder, goto_input, start_fn):
        self.ctx, self.feeder = ctx, feeder
        self.widget, lay = page_scaffold("Preview", "Left: the captured input. Right: exactly what the virtual camera "
                                         "receives. Both previews show the same frame.")
        bar = QHBoxLayout()
        self.start_btn = QPushButton(tr("Start")); self.start_btn.setObjectName("primary")
        self.stop_btn = QPushButton(tr("Stop"))
        self.pause_btn = QPushButton(tr("Pause output")); self.pause_btn.setCheckable(True)
        self.pass_btn = QPushButton(tr("Emergency passthrough")); self.pass_btn.setObjectName("danger"); self.pass_btn.setCheckable(True)
        self.repl_btn = QPushButton(tr("Replacement on")); self.repl_btn.setCheckable(True); self.repl_btn.setChecked(True)
        self.face_btn = QPushButton(tr("Hide face")); self.face_btn.setCheckable(True)
        self.face_btn.setToolTip(tr("Pixelates every detected face in the camera output (adds a region on the "
                                    "Region Editor page; click again to remove it)."))
        self.logo_btn = QPushButton(tr("Logo")); self.logo_btn.setCheckable(True)
        self.logo_btn.setToolTip(tr("Puts a picture (PNG with transparency works best) in the top-right corner of the "
                                    "camera output. Move or resize it on the Region Editor page; click again to "
                                    "remove it."))
        from vcstudio.replacement import privacy_mode as pm
        self.privacy = QComboBox()
        for lv in pm.LEVELS:
            self.privacy.addItem(tr(pm.LABELS[lv]), lv)
        self.privacy.setToolTip(tr("Privacy mode (Ctrl+Alt+H): hides faces; screens, laptops, phones and books; and "
                                   "the background — whatever the level includes. Switching it off restores the "
                                   "scene as it was."))
        self.framing_btn = QPushButton(tr("Auto-framing")); self.framing_btn.setCheckable(True)
        self.framing_btn.setToolTip(tr("Crop to the people in view and follow them smoothly (zoom up to 2×); back to "
                                       "the full picture when nobody is there."))
        self.light_btn = QPushButton(tr("Lighting")); self.light_btn.setCheckable(True)
        self.light_btn.setToolTip(tr("Automatic exposure and white balance, metered on the faces: a dim room or a "
                                     "bright window behind you no longer leaves your face dark. Your images and "
                                     "backgrounds are not changed."))
        self.card_btn = QPushButton(tr("Card for a game…"))
        self.card_btn.setToolTip(tr("Put a photo into the frame a browser game or an emulator draws over the camera."))
        self.boxes = QCheckBox(tr("Show detections")); self.boxes.setChecked(True)
        src_btn = QPushButton(tr("Input settings…"))
        self.rec_btn = QPushButton(tr("● Record")); self.rec_btn.setCheckable(True)
        self.rec_btn.setToolTip(tr("Record the camera output (exactly what apps receive) to an MP4 file in "
                                   "Videos\\AI Virtual Camera Studio (Ctrl+Alt+F9)."))
        self.replay_btn = QPushButton(tr("Instant replay")); self.replay_btn.setCheckable(True)
        self.replay_btn.setToolTip(tr("Keep the last seconds of the output in memory (nothing is written to disk) so "
                                      "you can save a moment after it happened."))
        self.save_replay_btn = QPushButton(tr("Save replay"))
        self.save_replay_btn.setToolTip(tr("Save the kept seconds as an MP4 (Ctrl+Alt+F10)."))
        self.rec_note = QLabel(); self.rec_note.setObjectName("muted")
        # two rows: one row of all controls is wider than a 1100 px window in Russian
        for b in (self.start_btn, self.stop_btn, self.pause_btn, self.pass_btn, self.rec_btn, self.replay_btn,
                  self.save_replay_btn):
            bar.addWidget(b)
        bar.addStretch()
        bar.addWidget(src_btn)
        lay.addLayout(bar)
        bar2 = QHBoxLayout()
        for b in (self.repl_btn, self.privacy, self.face_btn, self.logo_btn, self.framing_btn, self.light_btn,
                  self.card_btn, self.boxes):
            bar2.addWidget(b)
        bar2.addStretch()
        lay.addLayout(bar2)
        # own line: a long source description in the button bar would widen the page past the window
        self.info = QLabel(); self.info.setObjectName("muted"); self.info.setWordWrap(True)
        lay.addWidget(self.info)
        lay.addWidget(self.rec_note)
        self.note = QLabel(); self.note.setWordWrap(True); self.note.setStyleSheet("color: #d29922;")
        self.note.hide()
        lay.addWidget(self.note)
        views = QHBoxLayout()
        self.original = VideoView("ORIGINAL")
        self.processed = VideoView("PROCESSED  →  AI Virtual Camera")
        for v in (self.original, self.processed):
            v.setMinimumHeight(380)
            views.addWidget(v)
        lay.addLayout(views, 1)
        self.stats = QLabel(); self.stats.setObjectName("muted"); self.stats.setWordWrap(True)
        lay.addWidget(self.stats)

        self.start_btn.clicked.connect(start_fn)
        self.stop_btn.clicked.connect(ctx.stop_capture)
        self.pause_btn.toggled.connect(lambda on: ctx.pipeline.set_paused(on))
        self.pass_btn.toggled.connect(lambda on: ctx.pipeline.set_passthrough(on, "manual (button)")
                                      if on != ctx.pipeline.passthrough else None)
        self.repl_btn.toggled.connect(lambda on: ctx.pipeline.set_replacement_enabled(on)
                                      if on != ctx.pipeline.replacement_enabled else None)
        self.boxes.toggled.connect(lambda on: setattr(self.feeder, "show_detections", on))
        self.face_btn.toggled.connect(self._toggle_face)
        self.logo_btn.toggled.connect(self._toggle_logo)
        self.privacy.currentIndexChanged.connect(self._privacy_changed)
        self.framing_btn.toggled.connect(
            lambda on: self.ctx.update_active_profile(lambda b: b.setdefault("framing", {}).__setitem__("enabled", on)))
        self.light_btn.toggled.connect(self._toggle_light)
        self.card_btn.clicked.connect(self.open_game_card)
        self.rec_btn.toggled.connect(lambda on: self.toggle_recording() if on != ctx.recording.recording else None)
        self.replay_btn.toggled.connect(lambda on: ctx.settings.set("recording.replay_enabled", on))
        self.save_replay_btn.clicked.connect(self.save_replay)
        self._saving = None               # thread writing an instant replay
        self._rec_msg = ""
        self._card_dialog = None
        src_btn.clicked.connect(goto_input)
        self._last_ver = -1
        self.timer = QTimer(self.widget)
        self.timer.timeout.connect(self.tick)
        self.timer.start(int(1000 / max(1, ctx.settings.get("ui.preview_max_fps"))))

    def _sync_buttons(self):
        p = self.ctx.pipeline
        for b, v in ((self.pause_btn, p.paused), (self.pass_btn, p.passthrough), (self.repl_btn, p.replacement_enabled)):
            if b.isChecked() != v:
                b.blockSignals(True); b.setChecked(v); b.blockSignals(False)
        self.repl_btn.setText(tr("Replacement on") if p.replacement_enabled else tr("Replacement off"))
        on = self._quick_face_on()
        if self.face_btn.isChecked() != on:
            self.face_btn.blockSignals(True); self.face_btn.setChecked(on); self.face_btn.blockSignals(False)
        on = self._logo_on()
        if self.logo_btn.isChecked() != on:
            self.logo_btn.blockSignals(True); self.logo_btn.setChecked(on); self.logo_btn.blockSignals(False)
        body = self.ctx.profiles.active()["body"]
        for btn, section in ((self.framing_btn, "framing"), (self.light_btn, "lighting")):
            on = bool((body.get(section) or {}).get("enabled"))
            if btn.isChecked() != on:
                btn.blockSignals(True); btn.setChecked(on); btn.blockSignals(False)
        from vcstudio.replacement import privacy_mode as pm
        lv = pm.level_of(self.ctx.profiles.active()["body"])
        if self.privacy.currentData() != lv:
            self.privacy.blockSignals(True); self.privacy.setCurrentIndex(self.privacy.findData(lv))
            self.privacy.blockSignals(False)

    # ---- recording / instant replay (vcstudio/core/recorder.py)
    def toggle_recording(self) -> None:
        rs = self.ctx.recording
        if rs.recording:
            st = rs.stop_recording()
            if st is not None:
                self._rec_msg = (tr("Recording failed: {e}", e=st.error) if st.state == "error" else
                                 tr("Saved {path} ({s} s)", path=st.path, s=f"{st.seconds:.0f}"))
        else:
            try:
                rs.start_recording()
                self._rec_msg = ""
            except OSError as e:
                self._rec_msg = tr("Recording failed: {e}", e=e)
        self._sync_recording()

    def save_replay(self) -> None:
        rs = self.ctx.recording
        if rs.replay is None:
            self._rec_msg = tr("Turn on Instant replay first: it keeps the last seconds from then on.")
            self._sync_recording()
            return
        if self._saving is not None and self._saving.is_alive():
            return
        self._rec_msg = tr("Saving the replay…")

        def work():
            st = rs.save_replay()
            self._rec_msg = (tr("Replay not saved: {e}", e=tr(st.error)) if st.state == "error" else
                             tr("Saved {path} ({s} s)", path=st.path, s=f"{st.seconds:.0f}"))
        from vcstudio.core import maintenance
        self._saving = maintenance.start("replay-save", work, heavy=False)
        self._sync_recording()

    def _sync_recording(self) -> None:
        rs = self.ctx.recording
        for b, v in ((self.rec_btn, rs.recording), (self.replay_btn, rs.replay is not None)):
            if b.isChecked() != v:
                b.blockSignals(True); b.setChecked(v); b.blockSignals(False)
        rec = rs.recorder
        if rec is not None and rs.recording:
            s = rec.stats
            self.rec_btn.setText(tr("■ Stop recording {t}", t=f"{int(s.seconds) // 60}:{int(s.seconds) % 60:02d}"))
        else:
            self.rec_btn.setText(tr("● Record"))
        self.save_replay_btn.setEnabled(rs.replay is not None)
        msg = self._rec_msg
        if rs.replay is not None and not msg:
            msg = tr("Instant replay: {s} s kept ({mb} MB)", s=f"{rs.replay.held_seconds:.0f}",
                     mb=f"{rs.replay.memory_bytes() / 1e6:.0f}")
        self.rec_note.setText(msg)
        self.rec_note.setVisible(bool(msg))

    def _toggle_light(self, on: bool) -> None:
        def mutate(b):
            b.setdefault("lighting", {})["enabled"] = on
            if on:                                   # metering on faces needs face detection
                b.setdefault("ai", {})["face_detection"] = "vision"
        self.ctx.update_active_profile(mutate)

    def _privacy_changed(self, *_):
        from vcstudio.replacement import privacy_mode as pm
        lv = self.privacy.currentData()
        self.ctx.update_active_profile(lambda b: pm.set_level(b, lv))
        if lv != "off" and not self.ctx.pipeline.replacement_enabled:
            self.ctx.pipeline.set_replacement_enabled(True)

    def _logo_on(self) -> bool:
        from vcstudio.roi.model import QUICK_LOGO_ID
        return any(r.id == QUICK_LOGO_ID and r.enabled for r in self.ctx.rois.rois())

    def _toggle_logo(self, on: bool, path: str | None = None) -> None:
        from pathlib import Path

        from vcstudio.replacement.assets import IMAGE_EXTS, AssetError
        from vcstudio.roi.model import set_quick_logo
        if not on:
            self.ctx.update_active_profile(lambda b: set_quick_logo(b, None))
            return
        if path is None:
            from PySide6.QtWidgets import QFileDialog
            exts = " ".join(f"*{e}" for e in sorted(IMAGE_EXTS))
            path, _ = QFileDialog.getOpenFileName(self.widget, tr("Choose a logo"), str(Path.home()),
                                                  tr("Images") + f" ({exts})")
        if not path:
            self.logo_btn.blockSignals(True); self.logo_btn.setChecked(False); self.logo_btn.blockSignals(False)
            return
        try:
            asset = self.ctx.assets.import_file(Path(path)).to_dict()
        except (AssetError, OSError) as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self.widget, tr("Logo"), f"{Path(path).name}: {tr(str(e))}")
            self.logo_btn.blockSignals(True); self.logo_btn.setChecked(False); self.logo_btn.blockSignals(False)
            return
        self.ctx.update_active_profile(lambda b: set_quick_logo(b, asset))
        if not self.ctx.pipeline.replacement_enabled:
            self.ctx.pipeline.set_replacement_enabled(True)

    def _quick_face_on(self) -> bool:
        from vcstudio.roi.model import QUICK_FACE_ID
        return any(r.id == QUICK_FACE_ID and r.enabled for r in self.ctx.rois.rois())

    def _toggle_face(self, on: bool) -> None:
        from vcstudio.roi.model import set_quick_face
        self.ctx.update_active_profile(lambda b: set_quick_face(b, on))
        if on and not self.ctx.pipeline.replacement_enabled:
            self.ctx.pipeline.set_replacement_enabled(True)

    def open_game_card(self) -> None:
        from vcstudio.ui.game_card_dialog import GameCardDialog
        if self._card_dialog is None:
            self._card_dialog = GameCardDialog(self.ctx, self.widget.window())
        self._card_dialog.show(); self._card_dialog.raise_(); self._card_dialog.activateWindow()

    def output_note(self) -> str:
        """Why the camera output looks unchanged, if it does."""
        p = self.ctx.pipeline
        if p.passthrough:
            return tr("Emergency passthrough is on: the camera gets the original video.")
        if not p.replacement_enabled:
            return tr("Replacement is off: the camera gets the original video.")
        if not [r for r in self.ctx.rois.rois() if r.enabled]:
            return tr("No regions yet, so the camera gets the original video. Click “Hide face”, or create a "
                      "region on the Region Editor page.")
        return ""

    def _black_input(self) -> bool:
        """Checked at most twice a second while the input is frozen."""
        import time
        now = time.monotonic()
        if now - getattr(self, "_black_t", 0) > 0.5:
            self._black_t = now
            f, _ = self.ctx.capture.slot.peek()
            self._black = f is not None and frame_is_black(f)
        return self._black

    def tick(self):
        cs = self.ctx.capture.stats()
        live = cs.state not in ("stopped", "error")
        self.start_btn.setEnabled(not live)
        for b in (self.stop_btn, self.pause_btn, self.pass_btn, self.repl_btn):
            b.setEnabled(live)
        self._sync_buttons()
        self._sync_recording()
        text, level = STATE_OVERLAY.get(cs.state, (cs.state, "idle"))
        if cs.state == "frozen" and self._black_input():
            text, level = BLACK_INPUT_HINT, "warn"
        self.original.set_overlay(text, level)
        if not live:
            for v in (self.original, self.processed):
                v.set_image(None); v.set_boxes([])
            self.processed.set_overlay("Not running", "idle")
            self.info.setText(tr(cs.state.replace("_", " ")))
            self.note.hide()
            self.stats.setText(tr(""))
            return
        if not self.widget.isVisible():
            return
        pair, ver = self.ctx.pipeline.processed.peek()
        if pair is None or ver == self._last_ver:
            return
        self._last_ver = ver
        src, out = pair
        maxw = max(320, int(max(self.original.width(), self.processed.width()) * self.original.devicePixelRatioF()))
        try:
            self.original.set_image(frame_to_qimage(src, maxw))
            self.processed.set_image(frame_to_qimage(out, maxw))
        except Exception as e:
            self.stats.setText(tr("Preview conversion failed: {e}", e=e))
            return
        self.feeder._update_boxes_for([self.original], src)
        p = self.ctx.pipeline.stats()
        po = {"paused": ("PAUSED: clients see a frozen frame", "warn"),
              "passthrough": (tr("PASSTHROUGH ({reason})", reason=tr(p.passthrough_reason)), "error")}.get(p.state, ("", "ok"))
        self.processed.set_overlay(*po)
        note = self.output_note()
        self.note.setText(note); self.note.setVisible(bool(note))
        self.info.setText(f"{cs.source_name} · {cs.width}×{cs.height} · " +
                          tr("in {a} fps · out {b} fps", a=f"{cs.measured_fps:.0f}", b=f"{p.output_fps:.0f}"))
        cam = {"connected": tr("camera {name} connected (sent {s}, dropped {d})", name=p.camera_device,
                               s=p.camera_sent, d=p.camera_dropped),
               "not_installed": tr("camera {name} not available: output visible here only", name=p.camera_device),
               "waiting": tr("camera {name}: waiting for the first frame", name=p.camera_device),
               "disabled": tr("camera output disabled"),
               "error": tr("camera error: {e}", e=p.camera_error)}.get(p.camera_state, p.camera_state)
        self.stats.setText(tr("Pipeline {state} · latency {lat} ms (p95 {p95}) · processing {proc} ms ({backend} render "
                              "{render} ms, GPU {gpu} ms) · {regions} region(s) · skipped inputs {skipped}",
                              state=tr(p.state), lat=f"{p.latency_ms:.1f}", p95=f"{p.latency_ms_p95:.1f}",
                              proc=f"{p.process_ms:.1f}", backend=p.backend.upper() or "—", render=f"{p.render_ms:.1f}",
                              gpu=f"{p.gpu_ms:.2f}", regions=p.regions, skipped=p.inputs_skipped) + f" · {cam}")
