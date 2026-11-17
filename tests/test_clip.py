"""CLIP helpers (experimental, not used by the app; numbers in docs/ACCURACY.md): pre-processing, crops, the check's
decision rule with a stand-in encoder, and one real embedding when the model is downloaded."""
from __future__ import annotations

import numpy as np
import pytest

from vcstudio.ai import clip
from vcstudio.ai.detection import Detection


def test_preprocess_is_a_centre_crop_to_224_with_clip_normalisation():
    img = np.zeros((100, 300, 3), np.uint8); img[:, 120:180] = 255          # white centre fifth
    x = clip.preprocess(img)
    assert x.shape == (3, 224, 224) and x.dtype == np.float32
    assert x[:, 112, 112] == pytest.approx((1 - clip.MEAN) / clip.STD, abs=1e-3)
    assert x[:, 112, 0] == pytest.approx((0 - clip.MEAN) / clip.STD, abs=0.05)


def test_crop_adds_context_and_rejects_empty_boxes():
    img = np.zeros((100, 100, 4), np.uint8)
    assert clip.crop(img, Detection("a", 1, 40, 40, 60, 60), pad=0.5).shape[:2] == (41, 41)
    assert clip.crop(img, Detection("a", 1, 99, 99, 100, 100)) is None


class FakeEncoder:
    """Image i 'is' the label written into its first pixel; texts are one-hot over the queries + negatives."""

    def __init__(self, texts):
        self.texts = texts

    def embed_texts(self, texts):
        e = np.zeros((len(texts), 8), np.float32)
        for i, t in enumerate(texts):
            e[i, self.texts.index(t)] = 1
        return e

    def embed_images(self, crops):
        e = np.zeros((len(crops), 8), np.float32)
        for i, c in enumerate(crops):
            e[i, int(c[0, 0, 0])] = 1
        return e


def test_check_keeps_agreed_boxes_relabels_and_rejects():
    queries = ["cup", "bottle"]
    texts = [clip.TEMPLATE.format(q) for q in queries] + list(clip.NEGATIVES)
    img = np.zeros((100, 300, 3), np.uint8)
    img[:, :100] = 0; img[:, 100:200] = 1; img[:, 200:] = 2                  # cup | bottle | background
    dets = [Detection("cup", .9, 10, 10, 90, 90), Detection("cup", .8, 110, 10, 190, 90),
            Detection("bottle", .7, 210, 10, 290, 90), Detection("person", .9, 0, 0, 50, 50, source="yolox")]
    v = clip.ClipVerifier(FakeEncoder(texts), min_prob=0.3)
    out = v.verify(img, dets, queries)
    assert [(d.label, d.x0) for d in out] == [("cup", 10), ("bottle", 110), ("person", 0)]
    assert v.checked == 3 and v.rejected == 1
    assert v.verify(img, dets[:2], []) == dets[:2]                           # no queries: nothing judged


def test_image_query_labels_close_candidates():
    img = np.zeros((100, 200, 3), np.uint8); img[:, 100:] = 3
    q = clip.ImageQuery(FakeEncoder([]), threshold=0.9)
    q.add("logo", [np.full((10, 10, 3), 3, np.uint8)])
    out = q.match(img, [Detection("an object", .5, 10, 10, 90, 90), Detection("an object", .5, 110, 10, 190, 90)])
    assert [(d.label, d.x0) for d in out] == [("logo", 110)]
    with pytest.raises(ValueError):
        q.add("empty", [])


@pytest.mark.skipif(not clip.available(), reason="CLIP not downloaded (scripts/fetch_models.py --clip)")
def test_real_model_tells_a_bus_from_a_cat():
    from vcstudio.ai.testimages import load_bgra
    from vcstudio.core.resources import sample
    enc = clip.ClipEncoder("cpu_only")
    img = load_bgra(sample("bus.jpg"))
    e = enc.embed_images([img])
    t = enc.embed_texts([clip.TEMPLATE.format("bus"), clip.TEMPLATE.format("cat")])
    assert e.shape == (1, 512) and np.linalg.norm(e) == pytest.approx(1, abs=1e-4)
    assert (e @ t.T)[0, 0] > (e @ t.T)[0, 1]
