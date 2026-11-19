"""Small helpers shared by the Studio tabs."""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QLabel


class Worker(QObject):
    """Run a function off the UI thread; `done(kind, result, error)` arrives on the UI thread."""
    done = Signal(str, object, object)

    def run(self, kind: str, fn) -> threading.Thread:
        def work():
            try:
                res, err = fn(), None
            except Exception as e:                # noqa: BLE001 - shown to the user
                res, err = None, e
            self.done.emit(kind, res, err)
        t = threading.Thread(target=work, name=f"studio-{kind}", daemon=True)
        t.start()
        return t


def muted(text: str, **kw) -> QLabel:
    from vcstudio.i18n import tr
    lbl = QLabel(tr(text, **kw))
    lbl.setObjectName("muted")
    lbl.setWordWrap(True)
    return lbl
