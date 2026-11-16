"""One-click logo: a corner overlay region sized to the picture, drawn with its transparency."""
from pathlib import Path

import numpy as np
import pytest

from vcstudio.core.profiles import default_profile_body
from vcstudio.replacement.assets import AssetLibrary
from vcstudio.replacement.service import ReplacementService
from vcstudio.roi.model import QUICK_LOGO_ID, quick_logo_roi, rois_from_profile, set_quick_logo
from vcstudio.roi.resolver import resolve

DATA = Path(__file__).parent / "data"


@pytest.mark.parametrize("corner", ["top_right", "top_left", "bottom_right", "bottom_left"])
def test_logo_sits_in_the_corner_with_the_picture_aspect(corner):
    r = quick_logo_roi("a", 400, 200, corner)
    x0, y0, x1, y1 = r.bbox_px(1920, 1080)
    assert abs((x1 - x0) / (y1 - y0) - 2.0) < 0.01                       # 400×200 picture stays 2:1 on 16:9
    assert 0 < x0 < x1 < 1920 and 0 < y0 < y1 < 1080
    assert (x0 > 960) == corner.endswith("right") and (y0 < 540) == corner.startswith("top")
    assert abs(min(x0, 1920 - x1) - min(y0, 1080 - y1)) < 1.0            # same margin in pixels on both sides


def test_logo_toggle_roundtrip_keeps_one_region_and_the_asset():
    b = default_profile_body()
    asset = {"id": "logo1", "name": "logo", "file": "logo1.png", "kind": "image", "width": 300, "height": 300}
    set_quick_logo(b, asset)
    set_quick_logo(b, asset, "bottom_left")                              # again: replaced, not duplicated
    rois = rois_from_profile(b)
    assert [r.id for r in rois] == [QUICK_LOGO_ID] and rois[0].cx < 0.5 and rois[0].cy > 0.5
    assert [a["id"] for a in b["assets"]] == ["logo1"]
    assert rois[0].replacement["mode"] == "overlay" and rois[0].replacement["asset_id"] == "logo1"
    set_quick_logo(b, None)
    assert rois_from_profile(b) == [] and [a["id"] for a in b["assets"]] == ["logo1"]   # asset stays in the library


def test_logo_is_drawn_in_the_corner_only(paths):
    lib = AssetLibrary(paths.assets_dir)
    a = lib.import_file(DATA / "logo_alpha.png")
    b = default_profile_body()
    set_quick_logo(b, a.to_dict())
    svc = ReplacementService(lib, "cpu")
    svc.load_profile(b)
    assert svc.active()
    w, h = 1280, 720
    img = np.full((h, w, 4), 90, np.uint8)
    img[..., 3] = 255
    regions = resolve(rois_from_profile(b), [], w, h)
    out = svc.render(img.copy(), regions)
    changed = np.abs(out[..., :3].astype(int) - 90).max(axis=2) > 0
    x0, y0, x1, y1 = (int(round(v)) for v in regions[0].bbox)
    assert changed[y0:y1, x0:x1].any()
    outside = changed.copy(); outside[max(0, y0 - 2):y1 + 2, max(0, x0 - 2):x1 + 2] = False
    assert not outside.any() and x0 > w // 2 and y1 < h // 2


def test_preview_logo_button_adds_and_removes_the_logo(qtbot, paths, monkeypatch):
    from vcstudio.app.application import AppContext
    from vcstudio.ui.main_window import MainWindow
    warnings = []
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.warning", lambda *a, **k: warnings.append(a))
    ctx = AppContext(paths, configure_logging=False)
    w = MainWindow(ctx)
    qtbot.addWidget(w)
    try:
        page = next(p for p in w.pages if p.key == "preview")
        page._toggle_logo(True, str(DATA / "logo_alpha.png"))
        assert page._logo_on()
        page._sync_buttons()
        assert page.logo_btn.isChecked() and ctx.pipeline.replacement_enabled
        page.logo_btn.setChecked(False)
        assert not page._logo_on()
        page._toggle_logo(True, str(DATA / "not-an-image.txt"))          # wrong file: nothing added, no crash
        assert not page._logo_on() and warnings and not page.logo_btn.isChecked()
    finally:
        ctx.shutdown()
