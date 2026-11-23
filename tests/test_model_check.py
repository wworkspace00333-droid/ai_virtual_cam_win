"""0.6.0 Phase 1: compatibility check of any ONNX model, custom-model registry, real backend check."""
from __future__ import annotations

import shutil

import pytest

from vcstudio.ai import model_check as mc
from vcstudio.core.resources import models_dir

M = models_dir()


def _have(*names):
    return all((M / n).exists() for n in names)


@pytest.mark.parametrize("cat, inputs, shapes, fits", [
    ("object_detection", [{"shape": [1, 3, 416, 416]}], [[1, 3549, 85]], True),          # YOLOX
    ("object_detection", [{"shape": [1, 3, 640, 640]}], [[1, 84, 8400]], True),          # YOLO11 raw
    ("object_detection", [{"shape": [1, 3, 192, 192]}], [[1, 2, 192, 192]], False),      # a mask is not boxes
    ("instance_segmentation", [{"shape": [1, 3, 640, 640]}], [[1, 116, 8400]], False),   # no prototypes
    ("background_segmentation", [{"shape": [1, 3, 192, 192]}], [[1, 2, 192, 192]], True),
    ("body_pose", [{"shape": [1, 256, 256, 3]}], [[1, 195], [1, 1]], True),               # 39 x 5
    ("body_pose", [{"shape": [1, 3, 416, 416]}], [[1, 3549, 85]], False),                # detector is not a pose model
    ("hand_landmarks", [{"shape": [1, 224, 224, 3]}], [[1, 63]], True),                  # 21 x 3
    ("hand_landmarks", [{"shape": [1, 224, 224, 3]}], [[1, 30]], False),                 # 10 points only
    ("ocr", [{"shape": [1, 3, None, None]}], [[320, 320]], True),                        # DB map
    ("ocr", [{"shape": [1, 3, 48, 320]}], [[1, 2, 3, 4]], False),
    ("face_detection", [{"shape": [3, 4]}], [[1, 10, 4]], False),                       # no image input
    ("nonsense", [{"shape": [1, 3, 8, 8]}], [[1, 4]], False),
])
def test_validate_rules(cat, inputs, shapes, fits):
    ok, problems, used_by = mc.validate(cat, inputs, shapes)
    assert ok is fits and (not ok) == bool(problems)
    if not ok:
        assert used_by == ""


@pytest.mark.parametrize("name, cat", [
    ("yolox_nano.onnx", "object_detection"),
    ("human_segmentation_pphumanseg_2023mar.onnx", "background_segmentation"),
    ("face_detection_yunet_2023mar.onnx", "face_detection"),
    ("pose/pose_estimation_mediapipe_2023mar.onnx", "body_pose"),
    ("hands/handpose_estimation_mediapipe_2023feb.onnx", "hand_landmarks"),
    ("text/text_detection_en_ppocrv3_2023may.onnx", "ocr"),
])
def test_real_models_fit_their_category(name, cat):
    if not _have(name):
        pytest.skip(f"{name} not downloaded")
    rep = mc.check(M / name, cat, providers=["CPUExecutionProvider"], runs=3)
    assert rep.fits, rep.problems
    assert rep.best is not None and rep.best.used == "CPUExecutionProvider" and rep.best.median_ms > 0
    assert len(rep.sha256) == 64 and rep.size_bytes == (M / name).stat().st_size
    assert rep.run_shapes


def test_wrong_category_is_refused_with_reason():
    if not _have("human_segmentation_pphumanseg_2023mar.onnx"):
        pytest.skip("model missing")
    rep = mc.check(M / "human_segmentation_pphumanseg_2023mar.onnx", "object_detection",
                   providers=["CPUExecutionProvider"], runs=1)
    assert not rep.fits and "detection output" in rep.problems[0] and rep.used_by == ""


def test_broken_and_foreign_files(tmp_path):
    bad = tmp_path / "bad.onnx"
    bad.write_bytes(b"not a model at all")
    rep = mc.check(bad, "object_detection", providers=["CPUExecutionProvider"])
    assert not rep.fits and not rep.best and "does not run" in rep.problems[0]
    with pytest.raises(mc.ModelCheckError):
        mc.check(tmp_path / "x.txt", "ocr")


def test_register_list_toggle_verify_remove(tmp_path):
    src = M / "face_detection_yunet_2023mar.onnx"
    rep = mc.check(src, "face_detection", providers=["CPUExecutionProvider"], runs=2)
    dest = mc.register(rep, tmp_path, "my face/model")
    assert dest.name == "my-face-model.onnx"
    items = mc.list_custom(tmp_path)
    assert [i["name"] for i in items] == ["my-face-model"] and items[0]["enabled"] and items[0]["present"]
    mc.set_enabled(tmp_path, "my-face-model", False)
    assert not mc.list_custom(tmp_path)[0]["enabled"]
    assert mc.verify(tmp_path, "my-face-model")
    dest.write_bytes(dest.read_bytes()[:-1] + b"\x00")              # tampered after registration
    assert not mc.verify(tmp_path, "my-face-model")
    with pytest.raises(mc.ModelCheckError):
        mc.register(rep, tmp_path, "my-face-model")                   # name taken
    mc.unregister(tmp_path, "my-face-model")
    assert mc.list_custom(tmp_path) == []


def test_register_refuses_unfit_and_changed_files(tmp_path):
    src = tmp_path / "m.onnx"
    shutil.copy2(M / "face_detection_yunet_2023mar.onnx", src)
    rep = mc.check(src, "face_detection", providers=["CPUExecutionProvider"], runs=1)
    bad = mc.CompatReport(**{**rep.as_dict(), "providers": [], "fits": False, "problems": ["x"]})
    with pytest.raises(mc.ModelCheckError):
        mc.register(bad, tmp_path / "reg", "a")
    with src.open("ab") as f:
        f.write(b"\x00")
    with pytest.raises(mc.ModelCheckError, match="changed"):
        mc.register(rep, tmp_path / "reg", "a")


def test_backend_check_reports_what_really_ran():
    from vcstudio.core.hardware import verify_backends
    res = verify_backends(runs=2)
    cpu = [r for r in res if r["requested"] == "CPUExecutionProvider"]
    assert cpu and cpu[0]["ok"] and cpu[0]["real"] and cpu[0]["median_ms"] > 0
    for r in res:                                       # a listed provider that fell back is not "real"
        if r["ok"] and r["used"] != r["requested"]:
            assert not r["real"]


def test_custom_models_take_their_role_and_fall_back_when_changed(tmp_path):
    src = M / "human_segmentation_pphumanseg_2023mar.onnx"
    rep = mc.check(src, "background_segmentation", providers=["CPUExecutionProvider"], runs=1)
    dest = mc.register(rep, tmp_path, "my-seg")
    meta = mc.list_custom(tmp_path)[0]
    assert mc.usable_for(meta) == "background"
    assert mc.active_path(tmp_path, "background", "my-seg") == dest
    assert mc.active_path(tmp_path, "text", "my-seg") is None          # wrong role
    assert mc.active_path(tmp_path, "background", "") is None
    mc.set_enabled(tmp_path, "my-seg", False)
    assert mc.active_path(tmp_path, "background", "my-seg") is None    # disabled
    mc.set_enabled(tmp_path, "my-seg", True)
    dest.write_bytes(dest.read_bytes()[:-1] + b"\x01")
    assert mc.active_path(tmp_path, "background", "my-seg") is None    # changed after registration
    face = mc.check(M / "face_detection_yunet_2023mar.onnx", "face_detection", providers=["CPUExecutionProvider"], runs=1)
    assert mc.usable_for({"report": face.as_dict()}) == ""             # no module decodes this layout


def test_app_uses_the_chosen_custom_background_and_text_models(paths):
    from vcstudio.app.application import AppContext
    ctx = AppContext(paths, configure_logging=False)
    try:
        rep = mc.check(M / "human_segmentation_pphumanseg_2023mar.onnx", "background_segmentation",
                       providers=["CPUExecutionProvider"], runs=1)
        dest = mc.register(rep, ctx.paths.models_dir, "seg2")
        ctx.settings.set("models", {"background": "seg2", "text": ""})
        assert ctx.custom_model_path("background") == dest
        seg = ctx.background.person_factory()
        assert seg is not None and ctx.background.seg._factory is ctx.background.person_factory
        if (M / "text/text_detection_en_ppocrv3_2023may.onnx").exists():
            trep = mc.check(M / "text/text_detection_en_ppocrv3_2023may.onnx", "ocr", providers=["CPUExecutionProvider"],
                            runs=1)
            tdest = mc.register(trep, ctx.paths.models_dir, "det2")
            ctx.settings.set("models", {"background": "seg2", "text": "det2"})
            assert ctx.shield.custom_model() == tdest
        ctx.settings.set("models", {"background": "", "text": ""})
        assert ctx.custom_model_path("background") is None
    finally:
        ctx.shutdown()


def test_pose_and_hand_landmarkers_take_their_roles(tmp_path):
    for name, cat, role in (("pose/pose_estimation_mediapipe_2023mar.onnx", "body_pose", "pose"),
                            ("hands/handpose_estimation_mediapipe_2023feb.onnx", "hand_landmarks", "hands")):
        if not _have(name):
            continue
        rep = mc.check(M / name, cat, providers=["CPUExecutionProvider"], runs=1)
        assert rep.fits and rep.used_by.startswith(mc.ROLES[role]), rep.used_by
        dest = mc.register(rep, tmp_path, role + "-lm")
        assert mc.active_path(tmp_path, role, role + "-lm") == dest
    assert mc.validate("body_pose", [{"shape": [1, 3, 256, 256]}], [[1, 195]])[2] == ""   # other input layout


def test_app_hands_custom_landmarker_reaches_gestures_and_vision(paths):
    from vcstudio.app.application import AppContext
    if not _have("hands/handpose_estimation_mediapipe_2023feb.onnx"):
        pytest.skip("hand models not downloaded")
    ctx = AppContext(paths, configure_logging=False)
    try:
        rep = mc.check(M / "hands/handpose_estimation_mediapipe_2023feb.onnx", "hand_landmarks",
                       providers=["CPUExecutionProvider"], runs=1)
        mc.register(rep, ctx.paths.models_dir, "hl")
        ctx.settings.set("models", {"background": "", "text": "", "pose": "", "hands": "hl"})
        assert ctx.custom_model_path("hands") is not None and ctx.gestures._factory is not None
        assert ctx.vision.custom_model("hands") == ctx.custom_model_path("hands")
        tracker = ctx.gestures._factory()
        assert tracker is not None
        ctx.settings.set("models", {"background": "", "text": "", "pose": "", "hands": ""})
        assert ctx.gestures._factory is None
    finally:
        ctx.shutdown()
