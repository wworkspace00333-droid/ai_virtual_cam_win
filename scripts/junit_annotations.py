"""Print failed / errored tests from a pytest JUnit XML report as GitHub Actions error annotations.

    python scripts/junit_annotations.py build/junit.xml [--max 20]

Annotations show on the workflow run page, also to people who are not signed in (job logs are not). Each one names
the test, its file and line when known, and the first lines of the failure message. Exit code 0 always.
"""
from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def esc(s: str) -> str:            # workflow-command escaping
    return s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("report")
    ap.add_argument("--max", type=int, default=20)
    a = ap.parse_args()
    p = Path(a.report)
    if not p.exists():
        print(f"::warning::no JUnit report at {p} (pytest did not get to write it)")
        return 0
    n = 0
    for case in ET.parse(p).getroot().iter("testcase"):
        bad = case.find("failure")
        if bad is None:
            bad = case.find("error")
        if bad is None:
            continue
        n += 1
        if n > a.max:
            continue
        name = f"{case.get('classname', '')}::{case.get('name', '')}"
        msg = (bad.get("message") or "").strip()
        text = "\n".join((msg + "\n" + (bad.text or "")).strip().splitlines()[-25:])
        f = case.get("file") or case.get("classname", "").replace(".", "/") + ".py"
        line = case.get("line")
        loc = f"file={f}" + (f",line={int(line) + 1}" if line and line.isdigit() else "")
        print(f"::error {loc},title={esc(name)}::{esc(text[:3500])}")
    print(f"{n} failed / errored test(s)" + (f", first {a.max} annotated" if n > a.max else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
