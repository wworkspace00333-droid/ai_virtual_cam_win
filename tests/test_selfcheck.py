"""The bundle self-check (vcstudio.exe --self-check), run in-process on the source tree.

The installer build runs the same check on the frozen app; here it guards the check itself (it used to be exercised
only through a subprocess, so a broken check showed up first in an installer build).
"""
from __future__ import annotations

import json
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows bundle check")


def test_bundle_selfcheck_passes_its_required_checks(tmp_path, monkeypatch):
    monkeypatch.setenv("VCSTUDIO_HOME", str(tmp_path / "home"))
    from vcstudio.app.selftest import bundle_selfcheck
    out = tmp_path / "selfcheck.json"
    rc = bundle_selfcheck(str(out))
    d = json.loads(out.read_text(encoding="utf-8"))
    failed = [f"{r['check']}: {r['detail']}" for r in d["results"] if r["required"] and not r["ok"]]
    assert rc == 0 and not failed, failed
    names = {r["check"] for r in d["results"]}
    assert {"sample data", "models", "YuNet (OpenCV) face detection", "ONNX Runtime"} <= names
    assert any(n.startswith("model yolox_") for n in names)           # the shipped, permissively licensed detector
    assert d["frozen"] is False
