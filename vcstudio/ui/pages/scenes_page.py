"""Scenes page: switch complete looks with one click or Ctrl+Alt+1..9 (vcstudio/core/scene.py)."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QComboBox, QGridLayout, QHBoxLayout, QLabel, QPushButton, QWidget

from vcstudio.i18n import region_name, tr
from vcstudio.platform import INPUT_METHODS
from vcstudio.ui.widgets import Card, page_scaffold

THUMB_W, THUMB_H = 240, 135


def describe_input(inp: dict | None) -> str:
    if not inp:
        return tr("Same input as now")
    method = dict(INPUT_METHODS).get(inp.get("method"), inp.get("method") or "")
    extra = inp.get("video_path") or inp.get("screen_target") or inp.get("device_name_hint") or ""
    if extra and len(extra) > 40:
        extra = "…" + extra[-38:]
    return tr(method) + (f": {extra}" if extra else "")


class SceneCard(Card):
    def __init__(self, page, name: str):
        super().__init__(region_name(name))
        self.page, self.name = page, name
        self.thumb = QLabel(); self.thumb.setFixedSize(THUMB_W, THUMB_H); self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setStyleSheet("background: #111; color: #888;")
        self.layout_.addWidget(self.thumb)
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("Hotkey")))
        self.slot = QComboBox()
        self.slot.addItem(tr("auto"), None)
        for n in range(1, 10):
            self.slot.addItem(f"Ctrl+Alt+{n}", n)
        row.addWidget(self.slot, 1)
        self.layout_.addLayout(row)
        self.input = QLabel(); self.input.setObjectName("muted"); self.input.setWordWrap(True)
        self.layout_.addWidget(self.input)
        irow = QHBoxLayout()
        self.use_input = QPushButton(tr("Use the current input"))
        self.clear_input = QPushButton(tr("Same as now"))
        irow.addWidget(self.use_input); irow.addWidget(self.clear_input)
        self.layout_.addLayout(irow)
        self.switch_btn = QPushButton(tr("Switch to this scene"))
        self.layout_.addWidget(self.switch_btn)
        self.slot.currentIndexChanged.connect(self._slot_changed)
        self.use_input.clicked.connect(lambda: page.set_input(name, True))
        self.clear_input.clicked.connect(lambda: page.set_input(name, False))
        self.switch_btn.clicked.connect(lambda: page.switch(name))

    def refresh(self, active: bool, explicit_slot, effective_slot, inp, thumb) -> None:
        self.slot.blockSignals(True)
        self.slot.setCurrentIndex(max(0, self.slot.findData(explicit_slot)))
        self.slot.setItemText(0, tr("auto") + (f" (Ctrl+Alt+{effective_slot})" if effective_slot and
                                               not explicit_slot else ""))
        self.slot.blockSignals(False)
        self.input.setText(describe_input(inp))
        self.clear_input.setEnabled(inp is not None)
        self.switch_btn.setEnabled(not active)
        self.switch_btn.setText(tr("Active scene") if active else tr("Switch to this scene"))
        self.setStyleSheet("SceneCard { border: 2px solid #2f81f7; }" if active else "")
        if thumb is not None:
            h, w = thumb.shape[:2]
            img = QImage(thumb.data, w, h, w * 4, QImage.Format_ARGB32).copy()
            self.thumb.setPixmap(QPixmap.fromImage(img).scaled(THUMB_W, THUMB_H, Qt.KeepAspectRatio,
                                                               Qt.SmoothTransformation))
        else:
            self.thumb.setText(tr("No picture yet"))

    def _slot_changed(self, *_):
        self.page.set_slot(self.name, self.slot.currentData())


class ScenesPage:
    key, title = "scenes", "Scenes"

    def __init__(self, ctx):
        self.ctx = ctx
        self.widget, lay = page_scaffold("Scenes", tr(
            "A scene is a complete look — regions, replacements, background, AI settings and, if you want, its own "
            "input. Switching is instant and whole: the camera never shows half of one scene and half of another. "
            "Create, rename or export scenes on the Profiles page."))
        self.info = QLabel(); self.info.setObjectName("muted"); self.info.setWordWrap(True)
        lay.addWidget(self.info)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.grid_host)
        lay.addStretch()
        self.cards: dict[str, SceneCard] = {}
        # the profile store outlives this window: refresh only while the page's widgets still exist, and deliver
        # through a timer owned by the page (deleted with it) instead of a free-standing singleShot
        self._pending = QTimer(self.widget)
        self._pending.setSingleShot(True)
        self._pending.timeout.connect(self.refresh)
        ctx.profiles.add_listener(self._on_profiles)
        self.timer = QTimer(self.widget)
        self.timer.timeout.connect(self._tick)
        self.timer.start(2000)
        self.refresh()

    def _alive(self) -> bool:
        from shiboken6 import isValid
        return isValid(self.widget) and isValid(self._pending)

    def _on_profiles(self, *_):
        if self._alive():
            self._pending.start(0)

    def _rebuild(self, names: list[str]) -> None:
        for c in self.cards.values():
            c.setParent(None)
        self.cards = {n: SceneCard(self, n) for n in names}
        for i, n in enumerate(names):
            self.grid.addWidget(self.cards[n], i // 3, i % 3)

    def refresh(self) -> None:
        if not self._alive():
            return
        sm, prof = self.ctx.scenes, self.ctx.profiles
        names = prof.names()
        if list(self.cards) != names:
            self._rebuild(names)
        effective = {sm.name_for_slot(s): s for s in range(1, 10)}
        for n, card in self.cards.items():
            card.refresh(n == prof.active_name, sm.slot_of(n), effective.get(n), sm.input_of(n), sm.thumbnail(n))
        self.info.setText(tr("Active scene: {name}. Hotkeys work in other apps too when “System-wide” is on "
                             "(Settings).", name=region_name(prof.active_name or "—")))

    def _tick(self) -> None:
        if self.ctx.pipeline.running:
            self.ctx.scenes.remember_thumbnail()
        if self.widget.isVisible():
            self.refresh()

    def switch(self, name: str) -> None:
        self.ctx.scenes.switch(name)
        self.refresh()

    def set_slot(self, name: str, slot) -> None:
        self.ctx.scenes.set_slot(name, slot)
        self.refresh()

    def set_input(self, name: str, use_current: bool) -> None:
        sm = self.ctx.scenes
        sm.set_input(name, sm.capture_current_input() if use_current else None)
        self.refresh()
