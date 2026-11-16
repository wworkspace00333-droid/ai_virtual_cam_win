"""Tray icon: the app keeps feeding the camera while its window is hidden.

Menu: show / hide the window, start / stop processing, emergency passthrough, quit. Closing the window hides it to
the tray when settings ui.close_to_tray is on (the first time a notification says so); "Quit" really exits.
"""
from __future__ import annotations

from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QStyle, QSystemTrayIcon

from vcstudio.i18n import tr


def app_icon() -> QIcon:
    from vcstudio.core.resources import app_icon_path
    p = app_icon_path()
    if p is not None and p.exists():
        return QIcon(str(p))
    from PySide6.QtWidgets import QApplication
    return QApplication.style().standardIcon(QStyle.SP_ComputerIcon)


class AppTray:
    def __init__(self, win):
        self.win = win
        self.icon = QSystemTrayIcon(app_icon(), win)
        self.icon.setToolTip("AI Virtual Camera Studio")
        m = QMenu()
        self.a_show = QAction(tr("Show window"), m)
        self.a_run = QAction(tr("Start processing"), m)
        self.a_pass = QAction(tr("Emergency passthrough"), m); self.a_pass.setCheckable(True)
        self.a_quit = QAction(tr("Quit"), m)
        for a in (self.a_show, self.a_run, self.a_pass):
            m.addAction(a)
        m.addSeparator(); m.addAction(self.a_quit)
        self.menu = m
        self.icon.setContextMenu(m)
        self.a_show.triggered.connect(self.toggle_window)
        self.a_run.triggered.connect(self.toggle_processing)
        self.a_pass.triggered.connect(self.toggle_passthrough)
        self.a_quit.triggered.connect(win.quit_app)
        self.icon.activated.connect(self._activated)
        m.aboutToShow.connect(self.sync)
        self.told_hidden = False
        self.icon.show()

    @staticmethod
    def available() -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def _activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.toggle_window()

    def sync(self) -> None:
        ctx = self.win.ctx
        self.a_show.setText(tr("Hide window") if self.win.isVisible() else tr("Show window"))
        self.a_run.setText(tr("Stop processing") if ctx.pipeline.running else tr("Start processing"))
        self.a_pass.setChecked(ctx.pipeline.passthrough)
        self.a_pass.setEnabled(ctx.pipeline.running)

    def toggle_window(self) -> None:
        if self.win.isVisible():
            self.win.hide()
        else:
            self.win.showNormal(); self.win.raise_(); self.win.activateWindow()

    def toggle_processing(self) -> None:
        if self.win.ctx.pipeline.running:
            self.win.ctx.stop_capture()
        else:
            self.win.start_processing()

    def toggle_passthrough(self) -> None:
        p = self.win.ctx.pipeline
        p.set_passthrough(not p.passthrough, "manual (tray)")

    def notify_hidden(self) -> None:
        if not self.told_hidden:
            self.told_hidden = True
            self.icon.showMessage("AI Virtual Camera Studio",
                                  tr("Still running in the tray: the camera keeps working. Right-click the icon to quit."),
                                  QSystemTrayIcon.Information, 4000)

    def hide(self) -> None:
        self.icon.hide()
