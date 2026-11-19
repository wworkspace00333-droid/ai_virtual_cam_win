"""AI Models tab: check any ONNX model (vcstudio/ai/model_check.py), register it, and check the real AI backends."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from vcstudio.ai import model_check as mc
from vcstudio.i18n import tr
from vcstudio.ui.studio.common import Worker, muted
from vcstudio.ui.widgets import Card


def _p(name: str) -> str:
    return name.replace("ExecutionProvider", "")


class ModelsTab:
    def __init__(self, ctx):
        self.ctx = ctx
        self.models_dir: Path = ctx.paths.models_dir
        self.report: mc.CompatReport | None = None
        self.widget = QWidget()
        lay = QVBoxLayout(self.widget)
        # ---- backends
        be = Card("AI backends on this PC")
        be.layout_.addWidget(muted("Runs a small model on every execution provider and shows which one really ran "
                                   "it. A provider can be listed and still fall back to the processor."))
        row = QHBoxLayout()
        self.be_btn = QPushButton(tr("Check backends"))
        self.be_btn.clicked.connect(self.check_backends)
        row.addWidget(self.be_btn)
        row.addStretch(1)
        be.layout_.addLayout(row)
        self.be_text = QLabel("—")
        self.be_text.setWordWrap(True)
        be.layout_.addWidget(self.be_text)
        lay.addWidget(be)
        # ---- check a model
        ck = Card("Check a model")
        ck.layout_.addWidget(muted("Checks the model's inputs and outputs against the category, runs it on a sample, "
                                   "measures it on each backend and records its SHA-256. Nothing in the file is run "
                                   "as code."))
        r2 = QHBoxLayout()
        self.path = QLineEdit()
        self.path.setPlaceholderText(tr("Model file (.onnx)"))
        browse = QPushButton(tr("Choose…"))
        browse.clicked.connect(self._browse)
        self.cat = QComboBox()
        for k, label in mc.CATEGORIES.items():
            self.cat.addItem(tr(label), k)
        self.check_btn = QPushButton(tr("Check"))
        self.check_btn.clicked.connect(lambda: self.run_check())
        for w in (self.path, browse, self.cat, self.check_btn):
            r2.addWidget(w)
        r2.setStretch(0, 1)
        ck.layout_.addLayout(r2)
        self.result = QLabel("—")
        self.result.setWordWrap(True)
        ck.layout_.addWidget(self.result)
        r3 = QHBoxLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText(tr("Name"))
        self.reg_btn = QPushButton(tr("Add to my models"))
        self.reg_btn.setEnabled(False)
        self.reg_btn.clicked.connect(self.register)
        r3.addWidget(self.name, 1)
        r3.addWidget(self.reg_btn)
        ck.layout_.addLayout(r3)
        lay.addWidget(ck)
        # ---- registered
        lst = Card("My models")
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([tr(h) for h in ("Name", "Category", "Fastest backend", "Used by",
                                                               "Enabled")])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setMinimumHeight(120)
        lst.layout_.addWidget(self.table)
        r4 = QHBoxLayout()
        self.toggle_btn = QPushButton(tr("Enable / disable"))
        self.toggle_btn.clicked.connect(self.toggle)
        self.remove_btn = QPushButton(tr("Remove"))
        self.remove_btn.clicked.connect(self.remove)
        r4.addWidget(self.toggle_btn)
        r4.addWidget(self.remove_btn)
        r4.addStretch(1)
        lst.layout_.addLayout(r4)
        lay.addWidget(lst)
        lay.addStretch(1)
        self.worker = Worker()
        self.worker.done.connect(self._on_done)
        self.refresh_list()

    def _on_done(self, kind: str, res, err) -> None:
        if kind == "backends":
            self.be_btn.setEnabled(True)
            self.be_text.setText(tr("Check failed: {e}", e=err) if err is not None else self.backends_text(res))
        elif kind == "check":
            self.check_btn.setEnabled(True)
            if err is not None:
                self.result.setText(tr("Cannot check: {e}", e=err))
                self.report = None
            else:
                self.report = res
                self.result.setText(self.report_text(res))
                self.name.setText(Path(res.source).stem[:60])
            self.reg_btn.setEnabled(self.report is not None and self.report.fits)

    def check_backends(self) -> None:
        from vcstudio.core.hardware import verify_backends
        self.be_btn.setEnabled(False)
        self.be_text.setText(tr("Checking…"))
        self.worker.run("backends", verify_backends)

    @staticmethod
    def backends_text(res: list[dict]) -> str:
        lines = []
        for r in res:
            name = _p(r["requested"])
            if r.get("real"):
                lines.append(tr("{p}: works · {ms} ms", p=name, ms=r["median_ms"]))
            elif r.get("ok"):
                lines.append(tr("{p}: listed, but the model ran on {used}", p=name, used=_p(r["used"])))
            else:
                lines.append(tr("{p}: does not work ({e})", p=name, e=str(r.get("error", ""))[:120]))
        return "\n".join(lines)

    def _browse(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self.widget, tr("Choose a model"), "", "ONNX (*.onnx)")
        if f:
            self.path.setText(f)

    def run_check(self, path: str | None = None, category: str | None = None):
        p = path or self.path.text().strip()
        cat = category or self.cat.currentData()
        if not p:
            self.result.setText(tr("Choose a model file first."))
            return None
        self.check_btn.setEnabled(False)
        self.reg_btn.setEnabled(False)
        self.result.setText(tr("Checking…"))
        return self.worker.run("check", lambda: mc.check(Path(p), cat))

    @staticmethod
    def report_text(r: mc.CompatReport) -> str:
        ins = ", ".join(f"{i['name']} {['?' if d is None else d for d in i['shape']]}" for i in r.inputs)
        outs = ", ".join(f"{k} {v}" for k, v in r.run_shapes.items())
        prov = "; ".join(f"{_p(p.requested)} → {_p(p.used)} {p.median_ms} ms (p95 {p.p95_ms})" if p.ok else
                         f"{_p(p.requested)}: {tr('fails')}" for p in r.providers)
        head = tr("Fits the category.") if r.fits else tr("Does not fit: {why}", why="; ".join(r.problems))
        lines = [head, tr("Inputs: {v}", v=ins or "—"), tr("Outputs (sample run): {v}", v=outs or "—"),
                 tr("Backends: {v}", v=prov), f"SHA-256: {r.sha256}"]
        if r.license:
            lines.append(tr("Licence: {v}", v=r.license))
        lines.append(tr("Used by: {v}", v=r.used_by) if r.used_by else
                     tr("No module of the app decodes this layout yet: the model is kept and measured only."))
        return "\n".join(lines)

    def register(self) -> None:
        if self.report is None:
            return
        try:
            mc.register(self.report, self.models_dir, self.name.text())
            self.result.setText(self.result.text() + "\n" + tr("Added."))
            self.reg_btn.setEnabled(False)
        except (mc.ModelCheckError, OSError) as e:
            self.result.setText(self.result.text() + "\n" + str(e))
        self.refresh_list()

    def refresh_list(self) -> None:
        items = mc.list_custom(self.models_dir)
        self.table.setRowCount(len(items))
        for row, m in enumerate(items):
            rep = m.get("report", {})
            ok = [p for p in rep.get("providers", []) if p.get("ok")]
            best = min(ok, key=lambda p: p["median_ms"]) if ok else None
            vals = [m["name"], tr(mc.CATEGORIES.get(m.get("category", ""), m.get("category", ""))),
                    f"{_p(best['used'])} {best['median_ms']} ms" if best else "—",
                    rep.get("used_by") or tr("kept only"),
                    (tr("yes") if m.get("enabled") else tr("no")) + ("" if m.get("present") else
                                                                    " · " + tr("file missing"))]
            for col, v in enumerate(vals):
                self.table.setItem(row, col, QTableWidgetItem(str(v)))

    def _selected(self) -> str | None:
        r = self.table.currentRow()
        it = self.table.item(r, 0) if r >= 0 else None
        return it.text() if it else None

    def toggle(self) -> None:
        name = self._selected()
        if name:
            cur = next((m for m in mc.list_custom(self.models_dir) if m["name"] == name), None)
            if cur:
                mc.set_enabled(self.models_dir, name, not cur.get("enabled"))
            self.refresh_list()

    def remove(self) -> None:
        name = self._selected()
        if name:
            mc.unregister(self.models_dir, name)
            self.refresh_list()
