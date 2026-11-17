"""scripts/windows_pc_report.py --strict (roadmap P3.4, CI check of the installed app): fails on what must work on
any PC (AI on the CPU, the pipeline benchmark), only reports what depends on the machine (GPU, camera)."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def load():
    p = Path(__file__).resolve().parents[1] / "scripts" / "windows_pc_report.py"
    spec = importlib.util.spec_from_file_location("windows_pc_report_t", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_strict_rules():
    m = load()
    good = {"ai": [{"model": "yolox_nano", "provider": "CPU", "median_ms": 20},
                   {"model": "yolox_nano", "provider": "DirectML (x)", "error": "no GPU"}],
            "pipeline": {"modes": {}}, "camera": {"error": "not installed"}}
    assert m.problems(good) == []                                    # GPU and camera errors are only reported
    assert m.problems({"ai": [], "pipeline": {}})[0].startswith("ai: no model")
    bad = {"ai": [{"model": "yolox_s", "provider": "CPU", "error": "NoSuchFile"}], "pipeline": {"error": "boom"}}
    assert m.problems(bad) == ["ai yolox_s on CPU: NoSuchFile", "pipeline: boom"]
    assert m.problems({"ai": good["ai"], "pipeline": None}) == ["pipeline: no result"]
