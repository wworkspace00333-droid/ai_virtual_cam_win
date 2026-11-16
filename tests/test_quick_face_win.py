"""One-click face hiding end to end on Windows: YuNet finds the faces on the sample photo, the quick face region
covers them and the compositor pixelates exactly there (the rest of the picture is untouched)."""
from __future__ import annotations

import sys

import cv2
import numpy as np
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows (YuNet through ONNX Runtime)")


def test_hide_face_pixelates_the_faces_yunet_finds(paths):
    from vcstudio.ai.onnx_engine import YuNetFaceDetector, yunet_path
    from vcstudio.ai.tracking import TrackedObject
    from vcstudio.core.frame import Frame
    from vcstudio.core.profiles import default_profile_body
    from vcstudio.core.resources import sample_photo
    from vcstudio.replacement.assets import AssetLibrary
    from vcstudio.replacement.service import ReplacementService
    from vcstudio.roi.model import rois_from_profile, set_quick_face
    from vcstudio.roi.resolver import resolve
    if not yunet_path().exists():
        pytest.skip("YuNet model not fetched (scripts/fetch_models.py)")
    bgr = cv2.imread(str(sample_photo()))
    img = np.ascontiguousarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA))
    h, w = img.shape[:2]
    frame = Frame(1, 0.0, 0.0, w, h, array=img, source="test")
    faces, _ = YuNetFaceDetector().detect(frame)
    assert faces, "YuNet found no face on the sample photo"
    tracks = [TrackedObject(i + 1, "face", d.confidence, d.x0, d.y0, d.x1, d.y1, "tracked", 1.0, 0.0) for i, d in enumerate(faces)]

    body = default_profile_body()
    body["ai"]["face_detection"] = None
    set_quick_face(body, True)
    assert body["ai"]["face_detection"] and body["ai"]["enabled"]       # the button also switches faces on
    regions = resolve(rois_from_profile(body), tracks, w, h)
    assert len(regions) == len(faces)
    svc = ReplacementService(AssetLibrary(paths.assets_dir), "cpu")
    svc.load_profile(body)
    out = svc.render(img.copy(), regions)
    changed = np.abs(out[..., :3].astype(int) - img[..., :3].astype(int)).max(axis=2) > 0
    inside = np.zeros((h, w), bool)
    for r in regions:
        x0, y0, x1, y1 = (int(round(v)) for v in r.bbox)
        inside[max(0, y0 - 8):y1 + 8, max(0, x0 - 8):x1 + 8] = True     # feathered edge
    assert changed[inside].mean() > 0.3                                   # faces are pixelated
    assert not changed[~inside].any()                                      # nothing else changes
