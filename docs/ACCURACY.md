# Detection accuracy (Windows build)

Only measured numbers are listed, each with its machine and command. Speed figures from the development server
(Windows Server 2025, Intel Xeon 6975P-C, 2 cores / 4 threads, **no GPU**, ONNX Runtime CPU provider) are a lower
bound, not what a PC with a graphics card does; GPU figures go to `docs/HARDWARE_CHECKLIST.md` once measured.

## Built-in detectors on `tests/data/coco16` (16 COCO images, 214 boxes)

Command: `python scripts/eval_accuracy.py --data tests/data/coco16 --models ...` (2026-11-16, server, CPU).
COCO evaluation code of the app (`vcstudio/ai/evaluation.py`); detections at confidence 0.01.

| Model | Licence | Trained on these classes | mAP50 | mAP50-95 | Median ms / image (CPU) |
|---|---|---|---|---|---|
| YOLOX-Nano (shipped) | Apache-2.0 | yes | 0.377 | 0.252 | 35 |
| YOLOX-Tiny (shipped) | Apache-2.0 | yes | 0.511 | 0.351 | 91 |
| YOLOX-S (shipped) | Apache-2.0 | yes | 0.641 | 0.456 | 338 |
| YOLO11n (not shipped, AGPL; reference) | AGPL-3.0 | yes | 0.533 | 0.377 | 122 |
| YOLO11s (not shipped, AGPL; reference) | AGPL-3.0 | yes | 0.618 | 0.466 | 304 |

## "Any object" by text: OWLv2 base, 8-bit, no training

Command: `python scripts/eval_open_vocab.py --queries all --compute-units cpu_only` (2026-11-16, server, CPU).
The 80 COCO class names are the text queries, so the model also has to reject the classes that are not in a photo.

| Model | Queries | mAP50 | mAP50-95 | Best F1 (threshold) | Median ms / image (CPU) |
|---|---|---|---|---|---|
| OWLv2 base patch16 ensemble, int8 ONNX | 80 COCO names | 0.589 | 0.365 | 0.442 (0.30) | 7 848 |

Without any training on COCO it lands between YOLOX-Tiny and YOLOX-S, which were trained on exactly these
classes. 16 images is a small set: treat differences below ~0.05 as noise.

Sample photo (`tests/data/street.jpg`, CC0) with the queries "person", "bus", "red backpack" (threshold 0.15):
the people, both buses and the red backpack are found (red backpack 0.70); a second "red backpack" box at 0.46 is
the black backpack, so the threshold matters for colour words. SAM 2.1 tiny mask from that box: predicted IoU 0.88;
from a click on the backpack: 0.93. CPU timings: SAM image encoding 3.7 s, mask per box 0.1 s.

## Not measured yet (planned)

- The video test set of the release plan (20 short clips with 20 different objects: fruit, animals, household
  things; mask IoU, share of frames with the right object, false detections, jitter in px, FPS). It needs footage
  under a free licence or own recordings, and a GPU to run OWLv2 + SAM 2.1 per frame in reasonable time.
- Any speed on a GPU (DirectML / CUDA): `python scripts/windows_pc_report.py` on the target PC.
- Search by a sample image: the OWLv2 ONNX export used here has no image-query head (docs/AUDIT_WIN.md).
