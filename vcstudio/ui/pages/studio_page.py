"""Studio page (0.6.0): the professional tools in tabs, on top of the existing services (no second pipeline)."""
from __future__ import annotations

import logging

from PySide6.QtWidgets import QTabWidget

from vcstudio.i18n import tr
from vcstudio.ui.widgets import page_scaffold

log = logging.getLogger(__name__)


def _builders(ctx, window):
    from vcstudio.ui.studio import (audio_tab, automation_tab, color_tab, models_tab, multicam_tab, privacy_tab,
                                    remote_tab, vision_tab)
    return [("Multi-Camera", lambda: multicam_tab.MulticamTab(ctx)),
            ("AI Vision", lambda: vision_tab.VisionTab(ctx)),
            ("Privacy Shield", lambda: privacy_tab.PrivacyTab(ctx)),
            ("Color Studio", lambda: color_tab.ColorTab(ctx)),
            ("Automation", lambda: automation_tab.AutomationTab(ctx)),
            ("Audio Studio", lambda: audio_tab.AudioTab(ctx)),
            ("Remote Control", lambda: remote_tab.RemoteTab(ctx)),
            ("AI Models", lambda: models_tab.ModelsTab(ctx))]


class StudioPage:
    key, title = "studio", "Studio"

    def __init__(self, ctx, window=None):
        self.ctx = ctx
        self.window = window
        self.widget, lay = page_scaffold("Studio", "Professional tools: extra cameras and layouts, AI vision, "
                                                   "privacy shield, colour, automation, audio, remote control and "
                                                   "your own AI models. Everything runs on this PC.")
        self.tabs = QTabWidget()
        self.parts: list = []
        self.names: list[str] = []
        for title, make in _builders(ctx, window):
            try:
                part = make()
            except Exception:                    # one broken tab must not take the page down
                log.exception("studio tab %s failed to build", title)
                continue
            self.parts.append(part)
            self.names.append(title)
            self.tabs.addTab(part.widget, tr(title))
        lay.addWidget(self.tabs, 1)

    def part(self, title: str):
        return self.parts[self.names.index(title)] if title in self.names else None

    def tick(self) -> None:
        i = self.tabs.currentIndex()
        part = self.parts[i] if 0 <= i < len(self.parts) else None
        if part is not None and hasattr(part, "tick"):
            try:
                part.tick()
            except Exception:
                log.exception("studio tab tick failed")
