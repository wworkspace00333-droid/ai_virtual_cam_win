"""Optional model download: only on request, only from the recorded URLs, SHA-256 checked, nothing partial kept.

No test touches the network: the HTTP opener is replaced by an in-memory one."""
from __future__ import annotations

import hashlib
import io
import threading

import pytest

from vcstudio.ai import model_download as md


class FakeResponse(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}


def table(files: dict[str, bytes], bad: str = "") -> dict:
    return {name: (f"https://huggingface.co/x/{name}",
                   ("0" * 64) if name == bad else hashlib.sha256(data).hexdigest(), "Apache-2.0", 0)
            for name, data in files.items()}


def opener_for(files: dict[str, bytes], calls: list):
    def op(url):
        calls.append(url)
        return FakeResponse(files[url.rsplit("/x/", 1)[1]])
    return op


def test_downloads_missing_files_checks_hashes_and_skips_present_ones(tmp_path):
    files = {"a.onnx": b"A" * 3_000_000, "sub/b.json": b"{}"}
    calls, progress = [], []
    got = md.download(tmp_path, lambda n, d, t: progress.append((n, d, t)), table=table(files),
                      opener=opener_for(files, calls))
    assert sorted(got) == ["a.onnx", "sub/b.json"] and (tmp_path / "sub" / "b.json").read_bytes() == b"{}"
    assert progress[-1][0] in files and any(t == 3_000_000 for _, _, t in progress)
    assert md.download(tmp_path, table=table(files), opener=opener_for(files, calls)) == []   # nothing again
    assert len(calls) == 2 and not list(tmp_path.rglob("*.part"))


def test_checksum_mismatch_keeps_nothing(tmp_path):
    files = {"a.onnx": b"tampered"}
    with pytest.raises(md.DownloadError, match="checksum"):
        md.download(tmp_path, table=table(files, bad="a.onnx"), opener=opener_for(files, []))
    assert not list(tmp_path.rglob("*"))


def test_cancel_and_network_errors_keep_nothing(tmp_path):
    files = {"a.onnx": b"x" * 5_000_000}
    cancel = threading.Event()
    with pytest.raises(md.Cancelled):
        md.download(tmp_path, lambda *a: cancel.set(), cancel, table=table(files), opener=opener_for(files, []))
    assert not list(tmp_path.rglob("*.onnx*"))

    def broken(url):
        raise OSError("no route to host")
    with pytest.raises(md.DownloadError, match="no route"):
        md.download(tmp_path, table=table(files), opener=broken)


def test_only_the_recorded_host_is_used(tmp_path):
    t = {"a.onnx": ("https://example.com/a.onnx", "0" * 64, "MIT", 0)}
    with pytest.raises(md.DownloadError, match="unexpected source"):
        md.download(tmp_path, table=t, opener=lambda url: pytest.fail("must not open"))
    assert all(url.startswith("https://huggingface.co/") and len(h) == 64 for url, h, _, _ in md.OPEN_VOCAB.values())


def test_off_switches(monkeypatch, paths):
    from vcstudio.core.config import Settings
    s = Settings(paths)
    s.load()
    monkeypatch.delenv("VCS_NO_NETWORK", raising=False)
    assert md.network_allowed(s)
    s.set("privacy.allow_downloads", False)
    assert not md.network_allowed(s)
    s.set("privacy.allow_downloads", True)
    monkeypatch.setenv("VCS_NO_NETWORK", "1")
    assert not md.network_allowed(s)


def test_no_other_network_code_in_the_app():
    """The release rule: no network without an explicit user action and documentation. Only model_download.py and
    update_check.py (both on a click) may open a connection; anything new has to be added here on purpose."""
    import re
    from pathlib import Path
    pkg = Path(md.__file__).resolve().parents[1]
    pat = re.compile(r"^\s*(import|from)\s+(urllib\.request|http\.client|requests|httpx|aiohttp|socket|ftplib|smtplib)\b",
                     re.M)
    users = sorted(str(p.relative_to(pkg)).replace("\\", "/") for p in pkg.rglob("*.py")
                   if pat.search(p.read_text(encoding="utf-8")))
    assert users == ["ai/model_download.py", "app/update_check.py"], users


def test_fetch_models_script_uses_the_same_table():
    import importlib.util
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "scripts" / "fetch_models.py"
    spec = importlib.util.spec_from_file_location("fetch_models_t", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert {k: v[:2] for k, v in mod.OPEN_VOCAB.items()} == {k: v[:2] for k, v in md.OPEN_VOCAB.items()}


def test_detection_page_download_button(qtbot, paths, monkeypatch):
    """The button shows while the models are missing, downloads into the app's data folder, then hides."""
    from vcstudio.ai import open_vocab
    from vcstudio.app.application import AppContext
    from vcstudio.ui.main_window import MainWindow
    monkeypatch.delenv("VCS_NO_NETWORK", raising=False)
    user = paths.root / "models"
    monkeypatch.setattr(open_vocab, "user_models_dir", lambda: user)
    monkeypatch.setattr(open_vocab, "models_dir", lambda: user)
    files = {"owlv2_base_ensemble_int8.onnx": b"o", "owlv2_tokenizer.json": b"{}"}
    monkeypatch.setattr(md, "OPEN_VOCAB", table(files))
    ctx = AppContext(paths, configure_logging=False)
    w = MainWindow(ctx)
    qtbot.addWidget(w)
    try:
        page = next(p for p in w.pages if p.key == "detection")
        page._open_vocab_info()
        assert not page.dl_btn.isHidden() and not page.ov_on.isEnabled()
        calls = []
        page.start_download(opener=opener_for(files, calls))
        qtbot.waitUntil(lambda: page._dl_cancel_evt is None, timeout=5000)
        assert (user / "owlv2_tokenizer.json").exists() and len(calls) == 2
        assert page.dl_btn.isHidden() and page.ov_on.isEnabled()
        ctx.settings.set("privacy.allow_downloads", False)
        for f in files:
            (user / f).unlink()
        page._open_vocab_info()
        assert page.dl_btn.isHidden()                      # switched off: no button at all
    finally:
        ctx.shutdown()
