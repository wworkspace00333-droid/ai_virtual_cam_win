"""Remote Control tab: the local API / phone page (vcstudio/app/remote.py) — on/off, port, LAN opt-in, access code."""
from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget

from vcstudio.i18n import tr
from vcstudio.ui.studio.common import muted
from vcstudio.ui.widgets import Card


class RemoteTab:
    def __init__(self, ctx):
        self.ctx = ctx
        self.widget = QWidget()
        lay = QVBoxLayout(self.widget)
        c = Card("Remote control")
        c.layout_.addWidget(muted("A page for a phone or a second screen: scene buttons, recording, privacy, effects "
                                  "and camera switching. It runs inside this app; nothing goes through the "
                                  "internet. Every request needs the access code below."))
        self.enabled = QCheckBox(tr("Allow remote control"))
        c.layout_.addWidget(self.enabled)
        r = QHBoxLayout()
        r.addWidget(QLabel(tr("Port")))
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        r.addWidget(self.port)
        r.addStretch(1)
        c.layout_.addLayout(r)
        self.lan = QCheckBox(tr("Also allow other devices on my network (phone)"))
        c.layout_.addWidget(self.lan)
        self.lan_warn = QLabel(tr("Warning: anyone on this network who knows the access code can switch scenes, start "
                                  "recordings and turn privacy off. Use only on a network you trust; Windows Firewall "
                                  "may ask to allow the app."))
        self.lan_warn.setWordWrap(True)
        self.lan_warn.setStyleSheet("color: #d29922;")
        c.layout_.addWidget(self.lan_warn)
        lay.addWidget(c)
        a = Card("Access")
        self.url = QLineEdit()
        self.url.setReadOnly(True)
        a.layout_.addWidget(self.url)
        r2 = QHBoxLayout()
        self.code = QLineEdit()
        self.code.setReadOnly(True)
        self.code.setEchoMode(QLineEdit.Password)
        self.show_btn = QPushButton(tr("Show"))
        self.copy_btn = QPushButton(tr("Copy code"))
        self.new_btn = QPushButton(tr("New code"))
        for w in (self.code, self.show_btn, self.copy_btn, self.new_btn):
            r2.addWidget(w)
        r2.setStretch(0, 1)
        a.layout_.addLayout(r2)
        a.layout_.addWidget(muted("“New code” disconnects every phone or page using the old one. The code is stored "
                                  "encrypted for your Windows account."))
        self.status = QLabel("—")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        a.layout_.addWidget(self.status)
        lay.addWidget(a)
        lay.addStretch(1)
        self._loading = False
        self.load()
        self.enabled.toggled.connect(self.save)
        self.lan.toggled.connect(self.save)
        self.port.editingFinished.connect(self.save)
        self.show_btn.clicked.connect(self._toggle_show)
        self.copy_btn.clicked.connect(self._copy)
        self.new_btn.clicked.connect(self.new_code)

    def load(self) -> None:
        self._loading = True
        try:
            r = self.ctx.settings.get("remote") or {}
            self.enabled.setChecked(bool(r.get("enabled")))
            self.port.setValue(int(r.get("port") or 8799) if int(r.get("port") or 0) >= 1024 else 8799)
            self.lan.setChecked(bool(r.get("lan")))
            self.lan_warn.setVisible(self.lan.isChecked())
        finally:
            self._loading = False
        self._refresh()

    def save(self, *_):
        if self._loading:
            return
        self.ctx.settings.set("remote", {"enabled": self.enabled.isChecked(), "port": self.port.value(),
                                         "lan": self.lan.isChecked()})
        self.lan_warn.setVisible(self.lan.isChecked())
        self._refresh()

    def _refresh(self) -> None:
        srv = self.ctx.remote
        if srv.running:
            from vcstudio.app.remote import lan_address
            host = lan_address() if srv.lan else "127.0.0.1"
            self.url.setText(f"http://{host}:{srv.port}/")
            self.code.setText(srv.token)
        else:
            self.url.setText(tr("off") if not srv.error else srv.error)
            self.code.setText("")
        for w in (self.show_btn, self.copy_btn, self.new_btn):
            w.setEnabled(srv.running)

    def _toggle_show(self) -> None:
        pw = self.code.echoMode() == QLineEdit.Password
        self.code.setEchoMode(QLineEdit.Normal if pw else QLineEdit.Password)
        self.show_btn.setText(tr("Hide") if pw else tr("Show"))

    def _copy(self) -> None:
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.ctx.remote.token)

    def new_code(self) -> None:
        if self.ctx.remote.running:
            self.ctx.remote.renew_token()
            self._refresh()

    def tick(self) -> None:
        srv = self.ctx.remote
        self.status.setText(tr("Requests: {n} · live pages: {w}", n=srv.requests, w=srv.ws_clients)
                            + (f" · {srv.error}" if srv.error else ""))
