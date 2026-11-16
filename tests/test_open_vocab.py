"""'Any object' by text (OWLv2) and masks by box / click (SAM 2.1), on the public-domain sample photo.

The models are large (OWLv2 155 MB, SAM 2.1 62 MB, 8-bit); the tests skip without them. On a CPU one OWLv2 run takes
seconds, so each model is loaded once per module.
"""
from __future__ import annotations

import numpy as np
import pytest

from vcstudio.ai import open_vocab as ov

needs_owl = pytest.mark.skipif(not ov.available(), reason="OWLv2 not installed (scripts/fetch_models.py)")
needs_sam = pytest.mark.skipif(not ov.sam_available(), reason="SAM 2.1 not installed (scripts/fetch_models.py)")


@pytest.fixture(scope="module")
def photo():
    from vcstudio.ai.testimages import load_bgra
    from vcstudio.core.frame import Frame
    from vcstudio.core.resources import sample_photo
    img = load_bgra(sample_photo())
    return img, Frame(0, 0, 0, img.shape[1], img.shape[0], array=img)


@pytest.fixture(scope="module")
def owl_dets(photo):
    if not ov.available():
        pytest.skip("OWLv2 not installed")
    det = ov.OpenVocabDetector(["person", "bus", "red backpack"], "cpu_only")
    return det.detect(photo[1], 0.15, 0.3)[0]


@needs_owl
def test_text_queries_find_the_described_objects(owl_dets, photo):
    img = photo[0]
    by = {}
    for d in owl_dets:
        by.setdefault(d.label, []).append(d)
    assert len(by.get("person", [])) >= 3 and len(by.get("bus", [])) >= 1, {k: len(v) for k, v in by.items()}
    bag = max(by["red backpack"], key=lambda d: d.confidence)
    assert bag.confidence > 0.5
    patch = img[int(bag.y0):int(bag.y1), int(bag.x0):int(bag.x1), :3].reshape(-1, 3).astype(int)
    assert (patch[:, 2] > patch[:, 1] + 40).mean() > 0.3          # the best "red backpack" box is mostly red
    for d in owl_dets:
        assert 0 <= d.x0 < d.x1 <= img.shape[1] and 0 <= d.y0 < d.y1 <= img.shape[0] and d.source == "owlv2"


@needs_owl
def test_own_clip_tokenizer_matches_the_reference_tokenizer():
    """The app tokenizes in plain Python (no tokenizers / huggingface_hub at run time); same ids as the reference."""
    import json
    tokenizers = pytest.importorskip("tokenizers")
    path = ov.models_dir() / ov.OWL_TOKENIZER
    ref = tokenizers.Tokenizer.from_file(str(path))
    ref.enable_padding(length=ov.MAX_TOKENS, pad_id=0)
    ref.enable_truncation(ov.MAX_TOKENS)
    mine = ov.ClipTokenizer(path)
    coco = json.loads((ov.models_dir() / "yolox_s.json").read_text(encoding="utf-8"))["classes"]
    cases = coco + ["red cup", "a photo of a cat", "don't stop", "Ёлка 3D-printer", "café au lait", "日本の猫",
                    "R2-D2 droid", "50% off", "a very long description of an object that will certainly be truncated "
                    "by the tokenizer at sixteen"]
    for s in cases:
        r = ref.encode(s)
        assert mine.encode(s) == (r.ids, r.attention_mask), s


@needs_owl
def test_queries_are_validated_and_can_change_without_reload():
    det = ov.OpenVocabDetector(["cat"], "cpu_only")
    with pytest.raises(ValueError):
        det.set_queries(["  ", ""])
    det.set_queries(["dog", " bicycle "])
    assert det.queries == ["dog", "bicycle"] and det._ids.shape == (2, ov.MAX_TOKENS)


@needs_owl
@needs_sam
def test_sam_masks_from_a_box_and_a_click_cover_the_object(owl_dets, photo):
    img = photo[0]
    bag = max((d for d in owl_dets if d.label == "red backpack"), key=lambda d: d.confidence)
    sam = ov.PromptSegmenter("cpu_only")
    sam.set_image(img)
    m, iou = sam.mask_from_box((bag.x0, bag.y0, bag.x1, bag.y1))
    assert m.shape == img.shape[:2] and iou > 0.6
    b = ov.box_of(m)
    area = (bag.x1 - bag.x0) * (bag.y1 - bag.y0)
    assert b is not None and 0.2 * area < m.sum() < 1.2 * area      # an object inside its box, not the whole frame
    cx, cy = (bag.x0 + bag.x1) / 2, (bag.y0 + bag.y1) / 2
    mc, iou_c = sam.mask_from_click(cx, cy)
    assert mc[int(cy), int(cx)] and iou_c > 0.6                     # the clicked point is in its own mask
    local = ov.local_mask(m, (bag.x0, bag.y0, bag.x1, bag.y1))
    assert local.dtype == np.uint8 and local.max() == 255           # Detection.mask format (box-local silhouette)


@needs_owl
def test_engine_switches_to_open_vocabulary_while_enabled(tmp_path):
    from vcstudio.ai.detection import DetectionConfig
    from vcstudio.ai.engine import AIEngine
    from vcstudio.ai.model_manager import ModelManager
    from vcstudio.core.profiles import default_profile_body
    body = default_profile_body()
    assert DetectionConfig.from_profile(body).open_vocab is False
    body["ai"]["open_vocab"] = {"enabled": True, "queries": ["red cup", " ", "plant"]}
    cfg = DetectionConfig.from_profile(body)
    assert cfg.open_vocab and cfg.open_vocab_queries == ["red cup", "plant"]
    eng = AIEngine(None, ModelManager(tmp_path))
    cfg.compute_units = "cpu_only"
    eng._ensure_models(cfg, "balanced")
    first = eng._detector
    assert first.name == "owlv2-base" and eng.stats().model == "owlv2-base"
    cfg.open_vocab_queries = ["laptop"]
    eng._ensure_models(cfg, "balanced")
    assert eng._detector is first and first.queries == ["laptop"]    # new words without reloading the model
