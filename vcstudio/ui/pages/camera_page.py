from __future__ import annotations

import json
import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QFormLayout, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton,
                               QTableWidget, QTableWidgetItem)

from vcstudio import CAMERA_NAME
from vcstudio.camera import camera_manager as cm
from vcstudio.camera.output import OUTPUT_TARGETS, target_for
from vcstudio.core.system_info import camera_extensions
from vcstudio.ui.widgets import Card, StatCard, page_scaffold
from vcstudio.i18n import tr
from vcstudio.platform import IS_WINDOWS, text


class _Sig(QObject):
    done = Signal(str, str)     # (kind, message)


class VirtualCameraPage:
    key, title = "camera", "Virtual Camera"

    def __init__(self, ctx):
        self.ctx = ctx
        self.widget, lay = page_scaffold("Virtual Camera", tr(text("camera_page_intro"), camera=CAMERA_NAME))
        tgt = Card("Output camera")
        tf = QFormLayout()
        self.target = QComboBox()
        for k, t in OUTPUT_TARGETS.items():
            self.target.addItem(tr(t.label), k)
        tf.addRow(tr("Send processed video to"), self.target)
        tgt.layout_.addLayout(tf)
        self.target_note = QLabel(); self.target_note.setObjectName("muted"); self.target_note.setWordWrap(True)
        tgt.layout_.addWidget(self.target_note)
        lay.addWidget(tgt)
        self._sync_target()
        self.target.currentIndexChanged.connect(self._target_changed)

        st = Card("Status")
        g = QGridLayout(); g.setSpacing(10)
        self.k_host, self.k_ext = ("Setup tool", "Camera components") if IS_WINDOWS else ("Host app", "Extension")
        self.cards = {n: StatCard(n) for n in (self.k_host, self.k_ext, "Camera device", "Output")}
        for i, c in enumerate(self.cards.values()):
            g.addWidget(c, 0, i)
        st.layout_.addLayout(g)
        self.msg = QLabel(); self.msg.setWordWrap(True)
        st.layout_.addWidget(self.msg)
        row = QHBoxLayout()
        self.install_btn = QPushButton(tr("Install / update")); self.install_btn.setObjectName("primary")
        self.uninstall_btn = QPushButton(tr("Uninstall")); self.uninstall_btn.setObjectName("danger")
        self.approve_btn = QPushButton(tr("Open camera privacy settings" if IS_WINDOWS else "Open approval settings"))
        self.refresh_btn = QPushButton(tr("Refresh"))
        self.reinstall_btn = QPushButton(tr("Reinstall camera"))
        self.reinstall_btn.setToolTip(tr("Removes both camera devices and registers them again (administrator rights). "
                                         "Use it when other apps stopped seeing the camera."))
        self.reinstall_btn.setVisible(IS_WINDOWS)
        for b in (self.install_btn, self.reinstall_btn, self.uninstall_btn, self.approve_btn, self.refresh_btn):
            row.addWidget(b)
        row.addStretch()
        st.layout_.addLayout(row)
        lay.addWidget(st)

        self.name_edit = self.rename_btn = None
        if IS_WINDOWS:          # the user can give the camera devices their own name
            from vcstudio.platform.win.vcam_status import NAME_MAX, camera_name
            nc = Card("Camera name")
            nrow = QHBoxLayout()
            self.name_edit = QLineEdit(camera_name()); self.name_edit.setMaxLength(NAME_MAX)
            self.name_edit.setPlaceholderText(CAMERA_NAME)
            self.rename_btn = QPushButton(tr("Rename"))
            nrow.addWidget(QLabel(tr("Name other apps show"))); nrow.addWidget(self.name_edit, 1)
            nrow.addWidget(self.rename_btn)
            nc.layout_.addLayout(nrow)
            nnote = QLabel(tr("Applies to both cameras (DirectShow and Media Foundation) and needs administrator "
                              "rights once. Windows 11 adds “(Windows Virtual Camera)” to the Media Foundation "
                              "camera's name. Apps that already have the camera open show the new name after they "
                              "reopen it."))
            nnote.setObjectName("muted"); nnote.setWordWrap(True)
            nc.layout_.addWidget(nnote)
            lay.addWidget(nc)
            self.rename_btn.clicked.connect(self._rename)
            self.name_edit.returnPressed.connect(self._rename)

        chain = Card("Check the whole chain")
        chint = QLabel(tr(text("camera_chain_hint")))
        chint.setObjectName("muted"); chint.setWordWrap(True)
        chain.layout_.addWidget(chint)
        crow = QHBoxLayout()
        self.chain_btn = QPushButton(tr("Check the whole chain")); self.chain_btn.setObjectName("primary")
        self.reports_btn = QPushButton(tr("Open reports folder"))
        crow.addWidget(self.chain_btn); crow.addWidget(self.reports_btn); crow.addStretch()
        chain.layout_.addLayout(crow)
        self.chain_info = QLabel(); self.chain_info.setWordWrap(True)
        self.chain_info.setTextInteractionFlags(self.chain_info.textInteractionFlags() | Qt.TextSelectableByMouse)
        chain.layout_.addWidget(self.chain_info)
        lay.addWidget(chain)

        test = Card("Verify in other apps")
        hint = QLabel(tr(text("camera_verify_hint")))
        self.hint = hint
        hint.setObjectName("muted"); hint.setWordWrap(True)
        test.layout_.addWidget(hint)
        trow = QHBoxLayout()
        self.pattern_btn = QPushButton(tr("Start test pattern")); self.pattern_btn.setCheckable(True)
        self.loop_btn = QPushButton(tr("Run loopback test"))
        trow.addWidget(self.pattern_btn); trow.addWidget(self.loop_btn); trow.addStretch()
        test.layout_.addLayout(trow)
        self.test_info = QLabel(); self.test_info.setObjectName("muted"); self.test_info.setWordWrap(True)
        test.layout_.addWidget(self.test_info)
        lay.addWidget(test)

        if IS_WINDOWS:          # where to pick the camera in common apps, and a browser test page (no network)
            apps = Card("Check in other apps")
            from vcstudio.core.devices import own_camera_name
            nm = own_camera_name()
            steps = QLabel(tr(
                "<b>Windows Camera app</b>: open it and switch cameras until “{mf}” appears.<br>"
                "<b>Browser</b> (Chrome, Edge — and browser calls in Meet, Teams, Zoom): “Open browser test page” below.<br>"
                "<b>Zoom</b>: Settings → Video → Camera → “{mf}” or “{ds}”.<br>"
                "<b>Microsoft Teams</b>: Settings → Devices → Camera → “{mf}”.<br>"
                "<b>Discord</b>: User Settings → Voice &amp; Video → Camera → “{ds}”.<br>"
                "<b>OBS Studio</b>: Sources → + → Video Capture Device → “{ds}”.<br>"
                "Windows 11 shows the Media Foundation camera as “{mf}” to every app; the DirectShow camera “{ds}” "
                "(if installed) can be used by several apps at once. The Media Foundation camera can be shared too: "
                "apps that open cameras in shared mode run alongside each other and alongside one app that "
                "takes full control; only two apps that both want full control cannot use it at the same time.",
                mf=f"{nm} (Windows Virtual Camera)", ds=nm))
            steps.setWordWrap(True); steps.setTextFormat(Qt.RichText)
            apps.layout_.addWidget(steps)
            # the app writes only to its own camera on Windows; OBS turns it into OBS Virtual Camera
            obs_vc = QLabel(tr(
                "<b>Need “OBS Virtual Camera” instead?</b> In OBS Studio add Sources → + → Video Capture Device → "
                "“{ds}”, right-click it → Transform → Fit to screen, then click <b>Start Virtual Camera</b>. Apps "
                "that select “OBS Virtual Camera” now get this app's processed video. Do not use OBS Virtual Camera "
                "as this app's input at the same time (it would feed back into itself).", ds=nm))
            obs_vc.setWordWrap(True); obs_vc.setTextFormat(Qt.RichText)
            apps.layout_.addWidget(obs_vc)
            arow = QHBoxLayout()
            self.browser_btn = QPushButton(tr("Open browser test page"))
            self.wincam_btn = QPushButton(tr("Open the Windows Camera app"))
            arow.addWidget(self.browser_btn); arow.addWidget(self.wincam_btn); arow.addStretch()
            apps.layout_.addLayout(arow)
            lay.addWidget(apps)
            self.browser_btn.clicked.connect(self._open_test_page)
            self.wincam_btn.clicked.connect(lambda: __import__("os").startfile("microsoft.windows.camera:"))

        ext = Card("Registered camera components" if IS_WINDOWS else "Installed camera extensions (systemextensionsctl)")
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([tr("Name"), tr("File") if IS_WINDOWS else tr("Bundle ID"),
                                              tr("File exists") if IS_WINDOWS else tr("Team"), tr("State")])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMinimumHeight(110)
        ext.layout_.addWidget(self.table)
        lay.addWidget(ext)
        lay.addStretch()

        self._sig = _Sig(); self._sig.done.connect(self._done)
        self.sender = None
        self.install_btn.clicked.connect(lambda: self._async("install", cm.activate))
        self.reinstall_btn.clicked.connect(lambda: self._async("reinstall", cm.reinstall))
        self.uninstall_btn.clicked.connect(self._uninstall)
        self.approve_btn.clicked.connect(cm.open_approval_settings)
        self.refresh_btn.clicked.connect(self.refresh)
        self.pattern_btn.toggled.connect(self._toggle_pattern)
        self.loop_btn.clicked.connect(self._loopback)
        self.chain_btn.clicked.connect(self.check_chain)
        self.reports_btn.clicked.connect(self._open_reports)
        self._chain = None
        self.timer = QTimer(self.widget); self.timer.timeout.connect(self._tick); self.timer.start(500)
        self.refresh()

    # ---- output target
    def output_device(self) -> tuple[str, str]:
        o = self.ctx.settings.get("output")
        return o["camera_device"], o["camera_sink"]

    def _sync_target(self):
        dev, sink = self.output_device()
        key = target_for(dev, sink)
        if key == "custom" and self.target.findData("custom") < 0:
            self.target.addItem(tr("Custom: {dev} / {sink}", dev=dev, sink=sink), "custom")
        self.target.blockSignals(True)
        self.target.setCurrentIndex(self.target.findData(key))
        self.target.blockSignals(False)
        t = OUTPUT_TARGETS.get(key)
        note = tr(t.note) if t else tr("Set in settings.json (output.camera_device / output.camera_sink).")
        risk = self.ctx.feedback_loop_risk()
        if risk:   # shown inline (no modal): start_capture refuses this configuration anyway
            note = (f"<span style='color:#f85149'>{tr(risk)} " + tr("Switch the input to Direct OBS frame transfer "
                    "on the Input page.") + f"</span><br>{note}")
        self.target_note.setText(note)

    def _target_changed(self, _i):
        key = self.target.currentData()
        t = OUTPUT_TARGETS.get(key)
        if t is None:
            return
        if self.sender:
            self.pattern_btn.setChecked(False)
        s = self.ctx.settings
        s.set("output.camera_device", t.device, persist=False)
        s.set("output.camera_sink", t.sink, persist=False)
        if key == "obs" and self.ctx.feedback_loop_risk():
            # OBS Virtual Camera can't be input and output at once: read OBS through the plugin instead
            s.set("input.method", "obs_ipc", persist=False)
        s.save()
        self._sync_target()
        self.refresh()

    def selected_device_visible(self) -> bool:
        dev, _ = self.output_device()
        if dev == CAMERA_NAME:
            return self.status.device_visible
        try:
            from vcstudio.core.devices import list_video_devices
            return any(d.name == dev for d in list_video_devices())
        except Exception:
            return False

    def refresh(self):
        s = cm.status()
        self.status = s
        self._sync_target()
        self.cards[self.k_host].set("Installed" if s.host_app else "Not found",
                                    s.host_app or ("vcam-setup.exe" if IS_WINDOWS else "/Applications"),
                                    "ok" if s.host_app else "warn")
        state = {"activated_enabled": "ok", "installed": "ok"}.get(
            s.extension_state, "warn" if s.extension_state != "not_installed" else "idle")
        self.cards[self.k_ext].set(s.extension_state.replace("_", " ").title(), s.team_id and tr("team {id}", id=s.team_id), state)
        from vcstudio.core.devices import own_camera_name
        self.cards["Camera device"].set("Visible" if s.device_visible else "Not visible", own_camera_name(),
                                        "ok" if s.device_visible else "idle")
        self.msg.setText(tr(s.message))
        self.install_btn.setEnabled(s.host_app is not None)
        self.reinstall_btn.setEnabled(s.host_app is not None)
        self.uninstall_btn.setEnabled(s.extension_state != "not_installed")
        visible = self.selected_device_visible()
        dev, _ = self.output_device()
        if dev != CAMERA_NAME:
            self.cards["Camera device"].set("Visible" if visible else "Not visible", dev, "ok" if visible else "warn")
        for b in (self.pattern_btn, self.loop_btn):
            b.setEnabled(visible)
        if IS_WINDOWS:
            from vcstudio.platform.win.vcam_status import components
            rows = [(c.name, c.path or "—", tr("Yes") if c.file_exists else tr("No"), c.state.replace("_", " "))
                    for c in components()]
        else:
            rows = [(e["name"], e["bundle_id"], e["team_id"], e["state"]) for e in camera_extensions()]
        self.table.setRowCount(len(rows))
        for i, e in enumerate(rows):
            for j, v in enumerate(e):
                self.table.setItem(i, j, QTableWidgetItem(v))
        self.table.resizeColumnsToContents()

    def _async(self, kind, fn):
        self.install_btn.setEnabled(False)
        self.msg.setText(tr(text("camera_working")))

        def work():
            rc, out = fn()
            self._sig.done.emit(kind, f"{rc}|{out}")
        threading.Thread(target=work, daemon=True).start()

    def _rename(self):
        from vcstudio.platform.win.vcam_status import camera_name, valid_camera_name
        name = self.name_edit.text().strip()
        if name == camera_name():
            return
        if not valid_camera_name(name) or '"' in name:
            self.msg.setText(tr("The name must be 1-63 characters, without quotation marks or control characters."))
            return
        self.rename_btn.setEnabled(False)
        self._async("rename", lambda: cm.rename(name))

    def _uninstall(self):
        from vcstudio.core.devices import own_camera_name
        if QMessageBox.question(self.widget, tr("Uninstall"),
                                tr(text("camera_remove_question"), camera=own_camera_name())) == QMessageBox.Yes:
            self._async("uninstall", cm.deactivate)

    def _done(self, kind, payload):
        rc, _, out = payload.partition("|")
        self.refresh()
        if kind in ("install", "uninstall", "reinstall"):
            extra = {"2": text("camera_needs_approval"),
                     "3": text("camera_fallback") if IS_WINDOWS else text("camera_needs_restart")}.get(rc, "")
            self.msg.setText((f"{tr(kind.title())}: " + tr("exit {rc}.", rc=rc) + f" {out} {tr(extra)}").strip())
        elif kind == "rename":
            from vcstudio.platform.win.vcam_status import camera_name
            self.rename_btn.setEnabled(True)
            self.name_edit.setText(camera_name())
            self.msg.setText(tr("Camera renamed to “{name}”.", name=camera_name()) if rc == "0"
                             else (tr("Rename failed (exit {rc}).", rc=rc) + f" {out}").strip())
        elif kind == "loopback":
            self.test_info.setText(out)
            self.loop_btn.setEnabled(True)

    def _toggle_pattern(self, on: bool):
        from vcstudio.camera.output import CameraOutputError, VirtualCameraOutput
        from vcstudio.camera.test_sender import TestPatternSender
        if on:
            try:
                self.sender = TestPatternSender(VirtualCameraOutput(*self.output_device()),
                                                fps=self.ctx.settings.get("output.fps"))
                self.sender.start()
                self.pattern_btn.setText(tr("Stop test pattern"))
            except CameraOutputError as e:
                self.pattern_btn.setChecked(False)
                QMessageBox.warning(self.widget, tr("Virtual camera"), tr(str(e)))
        elif self.sender:
            self.sender.stop()
            self.sender = None
            self.pattern_btn.setText(tr("Start test pattern"))

    def _loopback(self):
        self.loop_btn.setEnabled(False)
        self.test_info.setText(tr("Running loopback test (6 s)…"))

        def work():
            from vcstudio.app.selftest import camera_loopback_selftest
            dev, sink = self.output_device()
            with tempfile.TemporaryDirectory(prefix="vcs-loop-") as d:
                out = Path(d) / "loop.json"
                camera_loopback_selftest(str(out), 6.0, dev, sink)
                r = json.loads(out.read_text(encoding="utf-8"))
            msg = (tr("PASS") if r.get('ok') else tr("FAIL")) + ": " + tr(
                "{got} of {sent} frames received in order={order}, latency median {med} ms / p95 {p95} ms, client "
                "resolution {res}", got=r.get('distinct_received'), sent=r.get('sent'), order=r.get('order_ok'),
                med=r.get('latency_ms_median'), p95=r.get('latency_ms_p95'), res=r.get('capture_resolution')) + (
                ". " + tr("Error: {e}", e=r['error']) if r.get("error") else "")
            self._sig.done.emit("loopback", f"0|{msg}")
        threading.Thread(target=work, daemon=True).start()

    def check_chain(self):
        from vcstudio.ui.chain_check import ChainCheck
        if self.sender:
            self.pattern_btn.setChecked(False)
        dev, sink = self.output_device()
        self.chain_btn.setEnabled(False)
        self.chain_info.setStyleSheet("")
        self._chain = ChainCheck(self.ctx, dev, sink, self.widget)
        self._chain.progress.connect(self.chain_info.setText)
        self._chain.finished.connect(self._chain_done)
        self._chain.start()

    def _chain_done(self, results):
        from vcstudio.ui.chain_check import summarize
        self.chain_btn.setEnabled(True)
        ok = bool(results) and all(r.get("ok") for _, r, _ in results)
        lines = [f"{title}: {summarize(r)}" for title, r, _ in results]
        if len(results) == 1:
            lines.append(tr("Your OBS video was not checked: OBS is not sharing frames (open OBS with the "
                            "vcstudio-obs plugin)."))
        lines.append(tr("Reports: {path}", path=str(self.ctx.paths.root / "reports")))
        self.chain_info.setStyleSheet("color: #3fb950;" if ok else "color: #f85149;")
        self.chain_info.setText("\n".join(lines))
        self.last_chain = results

    def _open_test_page(self):
        """Copy the local test page into the data folder (a plain path the browser can open) and open it."""
        import shutil
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from vcstudio.core.resources import camera_test_page
        src = camera_test_page()
        dst = self.ctx.paths.root / "camera_test.html"
        shutil.copyfile(src, dst)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(dst)))

    def _open_reports(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        d = self.ctx.paths.root / "reports"
        d.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(d)))

    def _tick(self):
        if not self.widget.isVisible():
            return
        if self.sender and self.sender.running:
            self.cards["Output"].set("Test pattern", tr("sent {s} · dropped {d}", s=self.sender.sent, d=self.sender.dropped), "ok")
        elif self.sender and self.sender.error:
            self.cards["Output"].set("Error", self.sender.error, "error")
        else:
            self.cards["Output"].set("Idle", "start capture to feed the camera")

    def shutdown(self):
        if self.sender:
            self.sender.stop()
