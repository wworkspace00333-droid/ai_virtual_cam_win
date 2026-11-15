# Models

The runtime app loads Core ML packages (`<name>.mlpackage` plus `<name>.json` metadata) from this folder and from `~/Library/Application Support/AIVirtualCameraStudio/models/`. They are not committed to git. Generate them with the separate conversion environment, so PyTorch never becomes a runtime dependency:

```bash
/opt/homebrew/bin/python3.12 -m venv .venv-convert
.venv-convert/bin/pip install "ultralytics>=8.3" coremltools
.venv-convert/bin/python scripts/convert_models.py            # yolo11n, yolo11s (NMS pipeline) + yolo11n-seg (raw)
.venv-convert/bin/python scripts/convert_models.py yolo11m yolo11m-seg    # Maximum Quality models
```

| Model | Size | Use |
|---|---|---|
| yolo11n | 5.2 MB | Low Resource mode ("Auto" there always takes the smallest model) |
| yolo11s | 18.2 MB | "Auto" in Performance / Balanced when the larger model doesn't fit the budget (the M2 Pro case) |
| yolo11m | 39 MB | "Auto" first choice in Performance (p95 ≤ 20 ms), Balanced and Maximum Quality (p95 ≤ 25 ms) |
| yolo11m-seg | 43 MB | Maximum Quality with object replacement |
| yolo11n-seg | ~6 MB | Instance segmentation. Loaded instead of the detector only while an ROI uses object (silhouette) replacement |

**Detection:** 80 COCO classes, including person, car, truck, bus, bicycle, motorcycle, apple, banana, orange, animals, furniture and electronics. Faces come from Apple Vision (`VNDetectFaceRectanglesRequest`), which finds face rectangles only; there is no identity recognition.

## Windows (ONNX Runtime)

The Windows build loads `<name>.onnx` plus `<name>.json` from the same folders (`%LOCALAPPDATA%\AIVirtualCameraStudio\models\` for user models). Convert with a separate environment (PyTorch stays out of the app):

```bat
py -3.12 -m venv C:\vcs-convert
C:\vcs-convert\Scripts\python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
C:\vcs-convert\Scripts\python -m pip install "ultralytics>=8.3" onnx onnxslim onnxruntime
C:\vcs-convert\Scripts\python scripts\convert_models.py --format onnx yolo11n yolo11s yolo11m yolo11n-seg yolo11m-seg
```

Exports are FP32, opset 17, 640×640, without NMS inside (NMS runs in `vcstudio/ai/onnx_engine.py`). They run on CUDA (`onnxruntime-gpu`), DirectML (any DirectX 12 GPU) or the CPU.

Accuracy on `tests/data/coco16` (mAP50, measured on Windows Server 2025, CPU provider, 2026-11-15): yolo11n 0.533, yolo11s 0.618, yolo11m 0.643, yolo11n-seg 0.515, yolo11m-seg 0.631 — the same as the Core ML models on macOS within FP16/FP32 noise.

**Faces on Windows:** `face_detection_yunet_2023mar.onnx` — YuNet from [OpenCV Zoo](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet), MIT license, SHA-256 `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`. It is committed to the repository (227 KB) because face detection is on by default. Face rectangles only; no identity recognition.

**License:** Ultralytics YOLO code and weights are **AGPL-3.0**. Distributing the app with these weights requires AGPL compliance or an Ultralytics Enterprise license. Any detector exported as a Core ML pipeline with `confidence`/`coordinates` outputs (Vision `VNRecognizedObjectObservation`) can replace them.
