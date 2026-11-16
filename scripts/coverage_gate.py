"""Coverage gate: the core packages together must stay at or above the threshold (lines + branches).

    python -m pytest --cov --cov-report=json:build/coverage.json
    python scripts/coverage_gate.py [build/coverage.json] [--min 80]

Core = the code that processes video and talks to Windows: vcstudio/core, ai, roi, replacement, camera, game and
platform/win. Not core: the GUI (driven by tests/test_ui*.py, omitted from coverage), vcstudio/app (command-line
entry points; the self-check is tested in-process, soak and main run as separate processes) and macOS-only code
(marked "# pragma: macos", measured on macOS). Prints a per-package table; exit 1 below the threshold.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

CORE = ("core", "ai", "roi", "replacement", "camera", "game", "platform/win")


def package(path: str) -> str:
    parts = path.replace("\\", "/").split("/")
    if len(parts) < 3:
        return "(root)"
    return f"{parts[1]}/{parts[2]}" if parts[1] == "platform" and len(parts) > 3 else parts[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("report", nargs="?", default="build/coverage.json")
    ap.add_argument("--min", type=float, default=80.0)
    a = ap.parse_args()
    d = json.loads(Path(a.report).read_text(encoding="utf-8"))
    acc: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])
    for f, v in d["files"].items():
        s = v["summary"]
        hit = s["covered_lines"] + s.get("covered_branches", 0)
        tot = s["num_statements"] + s.get("num_branches", 0)
        acc[package(f)][0] += hit
        acc[package(f)][1] += tot
    core = [0, 0]
    for k in sorted(acc):
        hit, tot = acc[k]
        flag = "core" if k in CORE else ""
        print(f"{k:16} {100 * hit / max(tot, 1):6.1f}%  {flag}")
        if k in CORE:
            core[0] += hit
            core[1] += tot
    pct = 100 * core[0] / max(core[1], 1)
    print(f"\ncore packages: {pct:.1f}% (minimum {a.min:.0f}%)   all measured code: {d['totals']['percent_covered']:.1f}%")
    import os
    if os.environ.get("GITHUB_ACTIONS"):          # visible on the run page without signing in
        level = "notice" if pct >= a.min else "error"
        print(f"::{level} title=Coverage::core packages {pct:.1f}% (minimum {a.min:.0f}%), "
              f"all measured code {d['totals']['percent_covered']:.1f}%")
    return 0 if pct >= a.min else 1


if __name__ == "__main__":
    sys.exit(main())
