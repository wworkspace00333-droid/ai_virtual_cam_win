"""Licence gate for the Windows build: fails (exit 1) when a GPL / AGPL component would ship in the installer.

    python scripts/check_licenses.py                 # runtime packages of this venv + the models that ship
    python scripts/check_licenses.py --app <folder>  # also scan a built app folder (PyInstaller dist)
    python scripts/check_licenses.py --json out.json

Checks
  1. Python packages: the dependency closure of the app's runtime requirements (requirements-windows.txt without
     build-only tools). Each licence must be on the allow list; LGPL is allowed only for the packages listed in
     LGPL_DYNAMIC (they ship as separate, replaceable DLLs). "X OR GPL" dual licences pass when X is allowed.
  2. Native libraries: no DLL whose name marks a GPL-only component (x264, x265, ... and GPL-only Qt modules) in the
     runtime packages or in the app folder; FFmpeg inside PyAV must report an LGPL build without --enable-gpl.
  3. Models: every model the installer ships (packaging/vcstudio_win.spec SHIPPED_MODELS) has an allowed licence in
     its metadata; Ultralytics models (AGPL-3.0) must not ship.
This is a technical gate, not legal advice: THIRD_PARTY_NOTICES.md records what ships and why it is allowed.
"""
from __future__ import annotations

import argparse
import importlib.metadata as md
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOTS = ["PySide6", "numpy", "scipy", "opencv-python-headless", "psutil", "onnxruntime-directml",
                 "onnxruntime-gpu", "av", "Pillow"]
ALLOWED = ["MIT", "MIT-CMU", "BSD", "BSD-2-Clause", "BSD-3-Clause", "0BSD", "Apache-2.0", "Apache 2.0", "Zlib",
           "CC0-1.0", "ISC", "PSF-2.0", "HPND", "Unlicense"]
LGPL_DYNAMIC = {"pyside6", "pyside6-essentials", "pyside6-addons", "shiboken6", "pyside6-pdf", "pyside6-webengine"}
# licence metadata that is not an SPDX expression, checked by hand (THIRD_PARTY_NOTICES.md)
KNOWN = {
    "scipy": "BSD-3-Clause",              # metadata holds the whole licence text
    "protobuf": "BSD-3-Clause",           # "3-Clause BSD License"
    "flatbuffers": "Apache-2.0",
    "opencv-python-headless": "Apache-2.0",
    "onnxruntime-directml": "MIT", "onnxruntime-gpu": "MIT",
    "mpmath": "BSD-3-Clause", "sympy": "BSD-3-Clause",
}
GPL_DLL = re.compile(r"(x264|x265|xvid|libfdk|rubberband|vidstab|frei0r|libzvbi|davs2|xavs2|"
                     r"Qt6(Charts|DataVisualization|Graphs|VirtualKeyboard|Quick3D\w*|Lottie|NetworkAuth|Coap|Mqtt|"
                     r"OpcUa|HttpServer|WaylandCompositor))", re.I)
DENY_LICENCE = re.compile(r"\bA?GPL\b|GNU (Affero )?General Public", re.I)


def norm(n: str) -> str:
    return re.sub(r"[-_.]+", "-", n).lower()


def closure() -> dict[str, md.Distribution]:
    seen: dict[str, md.Distribution] = {}

    def walk(name: str) -> None:
        k = norm(name)
        if k in seen:
            return
        try:
            d = md.distribution(name)
        except md.PackageNotFoundError:
            return
        seen[k] = d
        for r in d.requires or []:
            if "extra ==" in r:
                continue
            walk(re.split(r"[ ;<>=!~\[(]", r)[0])
    for r in RUNTIME_ROOTS:
        walk(r)
    return seen


def licence_of(k: str, d: md.Distribution) -> str:
    if k in KNOWN:
        return KNOWN[k]
    m = d.metadata
    if m.get("License-Expression"):
        return m["License-Expression"]
    if m.get("License"):
        return m["License"].splitlines()[0]
    return "; ".join(c.split("::")[-1].strip() for c in (m.get_all("Classifier") or []) if c.startswith("License"))


def licence_ok(k: str, lic: str) -> tuple[bool, str]:
    alts = [a.strip(" ()") for a in re.split(r"\s+OR\s+", lic)]
    for a in alts:
        parts = [p.strip(" ()") for p in re.split(r"\s+AND\s+", a)]
        if all(any(p.lower().startswith(x.lower()) for x in ALLOWED) for p in parts):
            return True, a
        if k in LGPL_DYNAMIC and all(p.upper().startswith("LGPL") for p in parts):
            return True, a + " (dynamically linked, replaceable)"
    return False, lic


def check_packages(report: list) -> int:
    bad = 0
    for k, d in sorted(closure().items()):
        lic = licence_of(k, d)
        ok, used = licence_ok(k, lic)
        report.append({"kind": "package", "name": k, "version": d.version, "licence": lic, "ok": ok, "as": used})
        if not ok:
            bad += 1
        # GPL-only DLLs inside an allowed package (PySide6-Addons carries Qt Charts, Quick 3D, ...) are a problem
        # only if they reach the app: PyInstaller collects what is imported. Listed here as warnings; --app decides.
        gpl = [Path(str(f)).name for f in (d.files or []) if GPL_DLL.search(Path(str(f)).name)
               and str(f).lower().endswith(".dll")]
        if gpl:
            report.append({"kind": "warn", "name": f"{k}: {len(gpl)} GPL-only DLLs", "licence": ", ".join(gpl[:4]) + " ...",
                           "ok": True, "as": "must not be in the app folder (checked with --app)"})
    return bad


def check_ffmpeg(report: list) -> int:
    try:
        import av
    except ImportError:
        return 0
    meta = av._core.library_meta["libavcodec"]
    conf, lic = meta.get("configuration", ""), meta.get("license", "")
    ok = "gpl" not in lic.lower().replace("lgpl", "") and "--enable-gpl" not in conf and "--enable-nonfree" not in conf \
        and "--enable-libx264" not in conf and "--enable-libx265" not in conf
    report.append({"kind": "ffmpeg", "name": f"FFmpeg in PyAV {av.__version__}", "licence": lic, "ok": ok,
                   "as": "LGPL build without GPL components" if ok else "GPL components enabled"})
    return 0 if ok else 1


def shipped_models() -> list[str]:
    spec = (ROOT / "packaging" / "vcstudio_win.spec").read_text(encoding="utf-8")
    m = re.search(r"SHIPPED_MODELS\s*=\s*\[([^\]]*)\]", spec)
    return re.findall(r'"([^"]+)"', m.group(1)) if m else []


def check_models(report: list, models_dir: Path) -> int:
    bad = 0
    sys.path.insert(0, str(ROOT))
    from fetch_models import MODELS                     # noqa: E402  (scripts/fetch_models.py)
    for stem in shipped_models():
        meta_p = models_dir / f"{stem}.json"
        meta = json.loads(meta_p.read_text(encoding="utf-8-sig")) if meta_p.exists() else {}
        lic = meta.get("license") or MODELS.get(f"{stem}.onnx", ("", "", ""))[2]
        src = meta.get("source", "")
        ok = bool(lic) and not DENY_LICENCE.search(lic) and "ultralytics" not in src.lower() \
            and not stem.lower().startswith("yolo11")
        report.append({"kind": "model", "name": stem, "licence": lic or "unknown", "ok": ok, "as": src})
        bad += not ok
    return bad


def check_app(report: list, app: Path) -> int:
    bad = 0
    for f in app.rglob("*.dll"):
        if GPL_DLL.search(f.name):
            report.append({"kind": "dll", "name": str(f.relative_to(app)), "licence": "GPL component", "ok": False,
                           "as": "in the app folder"})
            bad += 1
    allowed = set(shipped_models())
    for f in app.rglob("*.onnx"):
        if f.stem not in allowed:
            report.append({"kind": "model", "name": str(f.relative_to(app)), "licence": "not on SHIPPED_MODELS",
                           "ok": False, "as": "in the app folder"})
            bad += 1
    # the OBS plugin is GPL-2.0-or-later and ships by design (a separate work running inside OBS): every copy of
    # the binary must come with its complete source and the GPL text
    for dll in app.rglob("vcstudio-obs.dll"):
        src = dll.parent / "source"
        missing = [n for n in ("vcstudio-obs.c", "vcs_frame_shm.h", "COPYING", "scripts/build_obs_plugin_win.ps1")
                   if not (src / n).exists()]
        report.append({"kind": "gpl-src", "name": str(dll.relative_to(app)), "licence": "GPL-2.0-or-later",
                       "ok": not missing, "as": "source shipped" if not missing else "missing: " + ", ".join(missing)})
        bad += bool(missing)
    for need in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        if not list(app.rglob(need)):
            report.append({"kind": "notice", "name": need, "licence": "", "ok": False, "as": "missing in the app"})
            bad += 1
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", type=Path, help="built app folder to scan (dist\\AI Virtual Camera Studio)")
    ap.add_argument("--models", type=Path, default=ROOT / "models")
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    report: list[dict] = []
    bad = check_packages(report) + check_ffmpeg(report) + check_models(report, a.models)
    if a.app:
        bad += check_app(report, a.app)
    for r in report:
        print(f"{'ok  ' if r['ok'] else 'FAIL'}  {r['kind']:<8} {r['name']:<34} {r.get('version', ''):<12} {r['licence'][:50]}"
              + (f"  -> {r['as']}" if r.get("as") else ""))
    if a.json:
        a.json.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"\n{'FAILED' if bad else 'OK'}: {bad} blocking item(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
