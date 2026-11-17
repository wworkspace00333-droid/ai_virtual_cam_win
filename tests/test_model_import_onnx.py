"""ONNX model import without a YOLO model on disk: the layout checks, class names, trial run, install and remove.

test_model_import.py runs the same path on a real Ultralytics export, but needs yolo11n (AGPL, not shipped), so it
skips on CI and on a clean checkout. Here ONNX Runtime and the detector are stand-ins: what is tested is the decision
logic of the importer, not inference."""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

import numpy as np
import pytest

from vcstudio.ai import model_import as mi
from vcstudio.ai.backend import model_extensions

pytestmark = pytest.mark.skipif(".onnx" not in model_extensions(), reason="ONNX import is the Windows path")

NAMES80 = str({i: f"c{i}" for i in range(80)})
SAMPLE = np.zeros((48, 64, 4), np.uint8)


class FakeSession:
    def __init__(self, outputs, meta=None, inputs=None):
        self._outs = [NS(name=k, shape=s) for k, s in outputs.items()]
        self._meta = meta
        self._ins = inputs or [NS(name="images", shape=[1, 3, 640, 640], type="tensor(float)")]

    def get_inputs(self):
        return self._ins

    def get_outputs(self):
        return self._outs

    def get_modelmeta(self):
        return NS(custom_metadata_map=self._meta)


class FakeDetector:
    def __init__(self, labels, ms=2.0):
        self.labels, self.ms, self.calls = labels, ms, 0

    def detect(self, frame, conf, iou):
        self.calls += 1
        return [NS(label=lbl) for lbl in self.labels], self.ms + self.calls * 0.01


@pytest.fixture
def fake(monkeypatch, tmp_path):
    """fake(outputs, meta=..., labels=..., inputs=...) -> path of a dummy .onnx the stand-in session 'loads'."""
    import onnxruntime as ort

    import vcstudio.ai.backend as backend
    state = {}

    def make(outputs, meta=None, labels=("c0",), inputs=None, name="my export.onnx"):
        meta = {"names": NAMES80} if meta is None else meta
        monkeypatch.setattr(ort, "InferenceSession", lambda path, providers: FakeSession(outputs, meta, inputs))
        state["det"] = FakeDetector(list(labels))
        monkeypatch.setattr(backend, "make_detector", lambda lm: state["det"])
        p = tmp_path / name
        p.write_bytes(b"onnx")
        return p
    make.state = state
    return make


def test_raw_detector_is_described_and_timed(fake):
    src = fake({"output0": [1, 84, 8400]},
               {"names": NAMES80, "author": "me", "license": "MIT", "description": "cards", "Confidence threshold": "0.4"},
               labels=("c0", "c5"))
    rep = mi.inspect_model(src, sample=SAMPLE, runs=6)
    assert (rep.task, rep.format, rep.imgsz) == ("detect", "onnx-raw-detect", 640)
    assert len(rep.classes) == 80 and rep.classes[5] == "c5"
    assert rep.author == "me" and rep.license == "MIT" and rep.description == "cards"
    assert rep.suggested_name == "my-export" and rep.suggested_confidence == pytest.approx(0.4)
    assert rep.sample_detections == 2 and rep.sample_labels == ["c0", "c5"]
    assert fake.state["det"].calls == 6 and 0 < rep.median_ms <= rep.p95_ms
    assert rep.inputs == {"images": "tensor(float)"} and rep.outputs == {"output0": [1, 84, 8400]}
    assert json.loads(json.dumps(rep.as_dict()))["format"] == "onnx-raw-detect"


def test_end_to_end_and_segmentation_layouts(fake):
    rep = mi.inspect_model(fake({"output0": [1, 300, 6]}), sample=SAMPLE, runs=3)
    assert rep.format == "onnx-e2e-detect"
    rep = mi.inspect_model(fake({"output0": [1, 116, 8400], "output1": [1, 32, 160, 160]}), sample=SAMPLE, runs=3)
    assert (rep.task, rep.format) == ("segment", "onnx-raw-seg")


def test_dynamic_input_size_defaults_to_640_and_bad_threshold_to_025(fake):
    ins = [NS(name="images", shape=["batch", 3, "h", "w"], type="tensor(float)")]
    rep = mi.inspect_model(fake({"output0": [1, 84, "anchors"]}, {"names": NAMES80, "Confidence threshold": "x"},
                                inputs=ins), sample=SAMPLE, runs=3)
    assert rep.imgsz == 640 and rep.suggested_confidence == pytest.approx(0.25)
    assert rep.outputs == {"output0": [1, 84, -1]}


@pytest.mark.parametrize("outputs,meta,inputs,why", [
    ({"output0": [1, 84, 8400]}, {}, None, "class names"),
    ({"output0": [1, 84, 8400]}, {"names": "not python"}, None, "class names"),
    ({"output0": [1, 85, 8400]}, None, None, "81 classes but the model names 80"),
    ({"output0": [1, 100, 8400], "output1": [1, 32, 160, 160]}, None, None, "64 classes but the model names 80"),
    ({"output0": [1, 30, 8400], "output1": [1, 32, 160, 160]}, None, None, "do not fit YOLO-seg"),
    ({"a": [1, 84], "b": [1, 84]}, None, None, "unsupported outputs a[1, 84], b[1, 84]"),
    ({"output0": [1, 84, 8400]}, None, [NS(name="x", shape=[1, 640, 640], type="t")], "exactly one image input"),
    ({"output0": [1, 84, 8400]}, None, [NS(name="x", shape=[1, 1, 64, 64], type="t")], "exactly one image input"),
])
def test_layouts_the_app_cannot_run_are_rejected(fake, outputs, meta, inputs, why):
    with pytest.raises(mi.ModelImportError, match=why.replace("[", r"\[").replace("]", r"\]")):
        mi.inspect_model(fake(outputs, meta, inputs=inputs), sample=SAMPLE, runs=3)


def test_class_names_from_list_metadata(fake):
    rep = mi.inspect_model(fake({"output0": [1, 6, 100]}, {"names": "['ace', 'king']"}, labels=("king",)),
                           sample=SAMPLE, runs=3)
    assert rep.classes == ["ace", "king"] and rep.sample_labels == ["king"]


def test_trial_run_failures_are_import_errors(fake, monkeypatch):
    src = fake({"output0": [1, 84, 8400]}, labels=("not-a-class",))
    with pytest.raises(mi.ModelImportError, match="not in its class list"):
        mi.inspect_model(src, sample=SAMPLE, runs=3)

    def boom(frame, conf, iou):
        raise RuntimeError("bad shape")
    monkeypatch.setattr(fake.state["det"], "detect", boom)
    fake.state["det"].labels = ["c0"]
    with pytest.raises(mi.ModelImportError, match="fails when run on a frame: RuntimeError: bad shape"):
        mi.inspect_model(src, sample=SAMPLE, runs=3)


def test_missing_file_is_rejected(tmp_path):
    with pytest.raises(mi.ModelImportError, match="does not exist"):
        mi.inspect_model(tmp_path / "nope.onnx")


def test_clean_name():
    assert mi._clean_name("  my cards (v2)!.") == "my-cards--v2"
    assert mi._clean_name("...") == "model"
    assert len(mi._clean_name("a" * 99)) == 60


def test_install_writes_model_and_metadata_then_remove(fake, tmp_path):
    rep = mi.inspect_model(fake({"output0": [1, 6, 100]}, {"names": "['ace', 'king']"}, labels=("ace",)),
                           sample=SAMPLE, runs=3)
    models = tmp_path / "models"
    info = mi.install(rep, models, "deck", {"ace": " Ace ", "king": " ", "joker": "x"})
    meta = json.loads((models / "deck.json").read_text(encoding="utf-8"))
    assert (models / "deck.onnx").read_bytes() == b"onnx"
    assert meta["classes"] == ["Ace", "king"] and meta["source_classes"] == ["ace", "king"]
    assert meta["class_map"] == {"ace": "Ace"} and meta["confidence"] == pytest.approx(0.25)
    assert meta["origin"] == "user" and meta["source"] == "my export.onnx" and "median_ms" in meta["benchmark"]
    assert info.origin == "user" and info.name == "deck"
    assert sorted(p.name for p in models.iterdir()) == ["deck.json", "deck.onnx"]      # no temporary folder left

    mi.remove(info)
    assert list(models.iterdir()) == []
    with pytest.raises(mi.ModelImportError, match="built-in"):
        mi.remove(NS(origin="builtin", name="yolox_nano"))


def test_benchmark_on_a_loaded_model(fake):
    fake({"output0": [1, 84, 8400]})
    r = mi.benchmark(NS(), sample=SAMPLE, runs=5)
    assert 0 < r["median_ms"] <= r["p95_ms"]
