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

## CLIP check and picture queries (P2.8): measured, not enabled

CLIP ViT-B/32, 8-bit ONNX (Xenova, MIT), models/clip (`scripts/fetch_models.py --clip`), on coco16 with the
OWLv2 boxes above (`python scripts/eval_open_vocab.py --queries all --compute-units cpu_only --clip`, 2026-11-17,
server CPU, 14.5 ms per box):

| OWLv2 boxes | mAP50 | mAP50-95 | best F1 |
|---|---|---|---|
| as found (reference) | 0.589 | 0.365 | 0.442 |
| kept if CLIP p(query) >= 0.05 | 0.308 | 0.203 | 0.304 |
| kept if p >= 0.3 | 0.167 | 0.119 | 0.148 |
| re-ranked: confidence x sqrt(p) | 0.541 | 0.349 | 0.399 |

Why: CLIP's zero-shot top-1 on the ground-truth crops is 25 % over the 80 names (15 % below 48 px, 42-48 % above
96 px), lower than OWLv2's own judgement. Picture-to-picture search for the same class (one crop as the example,
crops of other photos ranked by cosine): mean AP 0.31, precision 0.04-0.07 at every threshold from 0.6 to 0.85.
Conclusion: with this model both features would make results worse, so they are not in the app. The code
(`vcstudio/ai/clip.py`) and the measurement stay, to test a stronger model (full precision or a larger ViT, a new
download) the same way. Not measured: matching one specific object (your own mug or logo) rather than a class, which
is easier for CLIP; coco16 has no such labels.

## 0.6.0 Studio analysers (first measurements, small)

- Head pose (`vcstudio/ai/head_pose.py`): exact on synthetic projections of the 3-D face model (±0.5° for yaw ±30°,
  pitch ±15°, roll 10°); on the two faces of `tests/data/bus.jpg` the angles are plausible (|yaw| < 20°). Not
  measured against ground truth on video.
- Body pose (`vcstudio/ai/pose.py`): on `bus.jpg` the nearest person gets a correct skeleton (head above shoulders above
  hips above ankles, all inside the person); the MediaPipe person detector found 1 of the 4 people there — it is
  made for 1-2 people near the camera.
- Text regions (`vcstudio/ai/text_detect.py`): 5 of 5 printed lines (0.6-1.2 OpenCV scale, ~15-35 px high) found on a
  synthetic page, 0 boxes on a smooth image; ~8 px text is usually missed; on `bus.jpg` the large bus lettering is
  found, small signs are missed.
