"""P0 fixes from the 0.5 audit (docs/WINDOWS_TECHNICAL_DEBT.md D21-D23): no lost model when the cache cannot be written,
no temp folders left by the self-check / benchmark, low disk space reported instead of silently failing."""
from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows (ONNX Runtime models)")


def test_model_loads_when_the_optimised_cache_cannot_be_written(tmp_path, caplog):
    """D23: on a full disk ONNX Runtime fails to save the optimised graph ("Protobuf serialization failed"); the model
    must still load, without the cache."""
    from vcstudio.ai.onnx_engine import _cache_file, create_session, provider_plan
    from vcstudio.core.resources import models_dir
    model = models_dir() / "yolox_nano.onnx"
    if not model.exists():
        pytest.skip("yolox_nano not fetched")
    cache = tmp_path / "cache"
    for name, opts in provider_plan("all"):        # every provider's .part target is a folder: the write must fail
        part = _cache_file(model, cache, name, opts).with_suffix(".part")
        part.mkdir(parents=True)
    sess, label = create_session(model, "all", cache)
    assert sess is not None and label
    assert "could not be written" in caplog.text
    assert not list(cache.glob("*.opt.onnx"))       # nothing half-written was kept as a cache entry


def test_low_disk_space_is_reported_and_a_full_database_does_not_stop_the_watchdog(paths):
    """D22: < 500 MB free -> one warning event and the status flag; back above -> one info event. A failing event write
    ("database or disk is full") must not break the watchdog."""
    import sqlite3

    from vcstudio.app.application import AppContext
    ctx = AppContext(paths, configure_logging=False)
    try:
        wd = ctx.watchdog
        free = {"mb": 120.0}
        wd.disk_free_fn = lambda: free["mb"]
        wd.check()
        assert wd.stats.disk_low and wd.stats.disk_free_mb == 120.0
        assert any("low disk space" in e for e in wd.stats.events)
        n = len(wd.stats.events)
        wd._next_disk = 0
        wd.check()
        assert len(wd.stats.events) == n                       # reported once, not every check

        class FullConn:                                         # the real journal, on a full disk
            def execute(self, *a, **k):
                raise sqlite3.OperationalError("database or disk is full")
        real = ctx.db._conn
        ctx.db._conn = FullConn()
        ctx.db.log_event("INFO", "test", "not recorded")         # never raises
        assert ctx.db.dropped_events == 1
        free["mb"] = 5000.0
        wd._next_disk = 0
        wd.check()                                              # does not raise
        assert not wd.stats.disk_low and "disk space back" in wd.stats.events[-1]
        ctx.db._conn = real
    finally:
        ctx.shutdown()


def test_settings_file_survives_a_failed_save(paths, monkeypatch):
    """A save that fails half-way (disk full) keeps the previous settings file intact and leaves no temp file."""
    import json
    import os

    from vcstudio.core.config import Settings
    s = Settings(paths)
    s.load()
    s.set("input.preferred_fps", 30)
    before = paths.settings_file.read_text(encoding="utf-8")

    def no_space(*a, **k):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(os, "fsync", no_space)
    with pytest.raises(OSError):
        s.set("input.preferred_fps", 60)
    monkeypatch.undo()
    assert paths.settings_file.read_text(encoding="utf-8") == before
    assert json.loads(before)["input"]["preferred_fps"] == 30
    assert [p.name for p in paths.root.glob(".settings.json.*")] == []


def test_selfcheck_leaves_no_temp_folder(tmp_path, monkeypatch):
    """D21: every self-check used to leave ~270 MB in %TEMP%\\vcs-selfcheck-*."""
    import tempfile

    from vcstudio.app.selftest import bundle_selfcheck
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))      # tempfile.mkdtemp() now creates folders here
    bundle_selfcheck(str(tmp_path / "out" / "selfcheck.json"))
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith("vcs-selfcheck-")]
    assert leftovers == []
    assert (tmp_path / "out" / "selfcheck.json").exists()
