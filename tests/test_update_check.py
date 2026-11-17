"""Checking for updates (roadmap P3.5): only on a click, one GET, versions compared correctly, links only to this
project's release pages, off without network permission. No test touches the network."""
from __future__ import annotations

import io
import json

import pytest

from vcstudio.app import update_check as uc


def opener_for(payload, calls=None):
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def op(url):
        if calls is not None:
            calls.append(url)
        return io.BytesIO(raw)
    return op


def rel(tag, pre=True, draft=False, url=None):
    return {"tag_name": tag, "prerelease": pre, "draft": draft,
            "html_url": url or f"https://github.com/{uc.REPO}/releases/tag/{tag}"}


def test_newer_release_is_found():
    calls = []
    info = uc.check("0.4.1", opener_for([rel("v0.4.1"), rel("v0.5.0"), rel("v0.4.0")], calls))
    assert calls == [uc.API_URL]
    assert info.newer and info.latest == "0.5.0" and info.prerelease
    assert info.url == f"https://github.com/{uc.REPO}/releases/tag/v0.5.0"


def test_same_or_older_is_not_newer_and_drafts_and_odd_tags_are_ignored():
    info = uc.check("0.4.1", opener_for([rel("v0.4.1"), rel("v0.9.0", draft=True), rel("nightly"),
                                         rel("v0.5.0-rc1"), rel("v0.3.2")]))
    assert not info.newer and info.latest == "0.4.1"
    assert uc.parse_version("v0.10.0") > uc.parse_version("v0.9.9")     # numeric, not text order


def test_links_only_to_this_projects_release_pages():
    info = uc.check("0.4.1", opener_for([rel("v1.0.0", url="https://evil.example/download")]))
    assert info.newer and info.url == uc.PAGE_PREFIX


def test_errors_are_reported_not_raised_raw():
    def boom(url):
        raise OSError("no route to host")
    with pytest.raises(uc.UpdateCheckError, match="no route"):
        uc.check("0.4.1", boom)
    with pytest.raises(uc.UpdateCheckError):
        uc.check("0.4.1", opener_for(b"<html>rate limited</html>"))
    with pytest.raises(uc.UpdateCheckError):
        uc.check("0.4.1", opener_for({"message": "Not Found"}))
    with pytest.raises(uc.UpdateCheckError, match="large"):
        uc.check("0.4.1", opener_for(b"[" + b" " * (uc.MAX_BYTES + 10) + b"]"))
    assert uc.check("0.4.1", opener_for([])).latest == ""


def test_permission(monkeypatch, paths):
    from vcstudio.core.config import Settings
    s = Settings(paths)
    monkeypatch.delenv("VCS_NO_NETWORK", raising=False)
    assert uc.allowed(s)
    s.set("privacy.allow_update_check", False)
    assert not uc.allowed(s)
    s.set("privacy.allow_update_check", True)
    monkeypatch.setenv("VCS_NO_NETWORK", "1")
    assert not uc.allowed(s)


def test_nothing_checks_at_start_up(monkeypatch, qtbot, paths):
    from vcstudio.app.application import AppContext
    from vcstudio.ui.main_window import MainWindow
    calls = []
    monkeypatch.setattr(uc, "_default_opener", lambda url: calls.append(url))
    ctx = AppContext(paths, configure_logging=False)
    w = MainWindow(ctx)
    qtbot.addWidget(w)
    try:
        qtbot.wait(300)
        assert calls == []
    finally:
        ctx.shutdown()


def test_settings_button(monkeypatch, qtbot, paths):
    from vcstudio.app.application import AppContext
    from vcstudio.ui.main_window import MainWindow
    monkeypatch.delenv("VCS_NO_NETWORK", raising=False)
    ctx = AppContext(paths, configure_logging=False)
    w = MainWindow(ctx)
    qtbot.addWidget(w)
    try:
        page = next(p for p in w.pages if p.key == "settings")
        assert page.update_btn.isEnabled()
        page.check_updates(opener=opener_for([rel("v9.0.0")]))
        qtbot.waitUntil(lambda: page.update_btn.isEnabled(), timeout=5000)
        assert "9.0.0" in page.update_info.text() and uc.PAGE_PREFIX in page.update_info.text()
        page.check_updates(opener=opener_for([rel("v0.0.1")]))
        qtbot.waitUntil(lambda: page.update_btn.isEnabled(), timeout=5000)
        assert "9.0.0" not in page.update_info.text()
    finally:
        ctx.shutdown()
    monkeypatch.setenv("VCS_NO_NETWORK", "1")
    ctx = AppContext(paths, configure_logging=False)
    w = MainWindow(ctx)
    qtbot.addWidget(w)
    try:
        page = next(p for p in w.pages if p.key == "settings")
        assert not page.update_btn.isEnabled() and page.update_info.text()
    finally:
        ctx.shutdown()
