"""Accuracy of the open-vocabulary detector (OWLv2) on a labelled YOLO-format set, without any training.

    python scripts/eval_open_vocab.py [--data tests/data/coco16] [--queries all|present] [--out build/open_vocab.json]

The class names of the labels are the text queries ("all": every COCO name, the model must also reject classes that
are not in the photo; "present": only names that occur in the set). Reports mAP50 / mAP50-95, the best F1 and its
threshold, and the median time per image on the provider used, with the same evaluation code as the built-in
detectors (vcstudio/ai/evaluation.py), so the numbers are comparable to scripts/eval_accuracy.py.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from vcstudio.ai.evaluation import Box, evaluate, load_yolo_labels
    from vcstudio.ai.model_manager import ModelManager
    from vcstudio.ai.open_vocab import OpenVocabDetector
    from vcstudio.ai.testimages import load_bgra
    from vcstudio.core.frame import Frame

    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "tests" / "data" / "coco16"))
    ap.add_argument("--queries", choices=("all", "present"), default="all")
    ap.add_argument("--compute-units", default="all")
    ap.add_argument("--out", default=str(ROOT / "build" / "open_vocab.json"))
    a = ap.parse_args()
    data = Path(a.data)
    names = ModelManager(ROOT / "build" / "tmp-models").builtin_classes()
    imgs = sorted(p for p in (data / "images").rglob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    gts_all = []
    for p in imgs:
        img = load_bgra(p)
        h, w = img.shape[:2]
        gts_all.append(load_yolo_labels(data / "labels" / p.relative_to(data / "images").with_suffix(".txt"), names, w, h))
    queries = names if a.queries == "all" else sorted({g.cls for gts in gts_all for g in gts})
    det = OpenVocabDetector(queries, a.compute_units)
    samples, ms = [], []
    t0 = time.time()
    for p, gts in zip(imgs, gts_all):
        img = load_bgra(p)
        h, w = img.shape[:2]
        dets, t = det.detect(Frame(0, 0, 0, w, h, array=img, source="eval"), conf=0.01, iou=0.5)
        ms.append(t)
        samples.append(([Box(d.label, d.x0, d.y0, d.x1, d.y1, d.confidence) for d in dets], gts))
        print(f"{p.name}: {len(dets)} boxes, {t:.0f} ms", flush=True)
    r = evaluate(samples)
    out = {"model": det.name, "backend": det.backend, "queries": a.queries, "n_queries": len(queries),
           "images": len(imgs), "gt_boxes": r.gt_boxes, "map50": r.map50, "map50_95": r.map50_95,
           "best_f1": r.best_f1, "best_threshold": r.best_threshold, "median_ms": round(statistics.median(ms), 1),
           "seconds": round(time.time() - t0, 1)}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
