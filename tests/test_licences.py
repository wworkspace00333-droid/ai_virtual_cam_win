"""Licence gate (scripts/check_licenses.py) and the permissively licensed default detector (YOLOX)."""
from __future__ import annotations

import collections
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_licenses as cl  # noqa: E402


@pytest.mark.parametrize("pkg,lic,ok", [
    ("numpy", "BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0", True),
    ("packaging", "Apache-2.0 OR BSD-2-Clause", True),
    ("pyside6", "LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only", True),     # LGPL option, dynamically linked
    ("somepkg", "LGPL-3.0-only", False),                                     # LGPL only where it is a replaceable DLL
    ("somepkg", "GPL-3.0-or-later", False),
    ("somepkg", "AGPL-3.0", False),
    ("somepkg", "MIT AND GPL-2.0", False),
])
def test_licence_rules(pkg, lic, ok):
    assert cl.licence_ok(pkg, lic)[0] is ok


@pytest.mark.parametrize("name,bad", [
    ("libx264-165-abc.dll", True), ("libx265.dll", True), ("Qt6Charts.dll", True), ("Qt6Quick3DUtils.dll", True),
    ("qtvirtualkeyboardplugin.dll", False), ("Qt6Core.dll", False), ("avcodec-63.dll", False), ("libdav1d.dll", False),
])
def test_gpl_dll_names(name, bad):
    assert bool(cl.GPL_DLL.search(name)) is bad


def test_ultralytics_models_never_ship():
    shipped = cl.shipped_models()
    assert shipped and not any(s.lower().startswith("yolo11") for s in shipped)
    assert {"yolox_nano", "face_detection_yunet_2023mar"} <= set(shipped)


def test_app_folder_scan_flags_gpl_and_unlisted_models(tmp_path):
    (tmp_path / "_internal" / "av.libs").mkdir(parents=True)
    (tmp_path / "_internal" / "av.libs" / "libx264-165.dll").write_bytes(b"")
    (tmp_path / "_internal" / "models").mkdir()
    (tmp_path / "_internal" / "models" / "yolo11n.onnx").write_bytes(b"")
    (tmp_path / "_internal" / "models" / "yolox_nano.onnx").write_bytes(b"")
    report: list = []
    bad = cl.check_app(report, tmp_path)
    names = {r["name"] for r in report if not r["ok"]}
    assert bad == 4            # x264, yolo11n, and both notice files missing
    assert any("libx264" in n for n in names) and any("yolo11n" in n for n in names)
    assert not any("yolox_nano" in n for n in names)


def test_gpl_obs_plugin_must_ship_with_its_source(tmp_path):
    plugin = tmp_path / "_internal" / "obs-plugin"
    plugin.mkdir(parents=True)
    (plugin / "vcstudio-obs.dll").write_bytes(b"")
    report: list = []
    cl.check_app(report, tmp_path)
    r = next(r for r in report if r["kind"] == "gpl-src")
    assert not r["ok"] and "COPYING" in r["as"]
    src = plugin / "source"
    (src / "scripts").mkdir(parents=True)
    for n in ("vcstudio-obs.c", "vcs_frame_shm.h", "COPYING", "scripts/build_obs_plugin_win.ps1"):
        (src / n).write_bytes(b"x")
    report = []
    cl.check_app(report, tmp_path)
    assert next(r for r in report if r["kind"] == "gpl-src")["ok"]


def test_plugin_source_files_carry_their_licences():
    c = (ROOT / "native" / "obs-plugin" / "vcstudio-obs.c").read_text(encoding="utf-8")
    h = (ROOT / "native" / "obs-plugin" / "vcs_frame_shm.h").read_text(encoding="utf-8")
    assert c.startswith("// SPDX-License-Identifier: GPL-2.0-or-later")
    assert h.startswith("// SPDX-License-Identifier: MIT")          # shared with the proprietary program
    assert "GNU GENERAL PUBLIC LICENSE" in (ROOT / "native" / "obs-plugin" / "COPYING").read_text(encoding="utf-8")


YOLOX = ROOT / "models" / "yolox_nano.onnx"


@pytest.mark.skipif(not YOLOX.exists(), reason="run scripts/fetch_models.py")
def test_yolox_finds_people_and_a_bus_on_the_sample_photo(tmp_path):
    from vcstudio.ai.backend import make_detector
    from vcstudio.ai.model_manager import ModelManager
    from vcstudio.ai.testimages import load_bgra
    from vcstudio.core.frame import Frame
    from vcstudio.core.resources import sample_photo
    mm = ModelManager(tmp_path)
    info = mm.resolve("yolox_nano")
    assert info.format == "onnx-yolox" and info.license == "Apache-2.0"
    det = make_detector(mm.load(info, "cpu_only"))
    img = load_bgra(sample_photo())
    dets, _ = det.detect(Frame(0, 0, 0, img.shape[1], img.shape[0], array=img), 0.35, 0.5)
    n = collections.Counter(d.label for d in dets)
    assert n["person"] >= 3 and n["bus"] >= 1, n
    for d in dets:                                   # boxes in frame coordinates, inside the image
        assert 0 <= d.x0 < d.x1 <= img.shape[1] and 0 <= d.y0 < d.y1 <= img.shape[0]
        assert d.source == "yolox"
