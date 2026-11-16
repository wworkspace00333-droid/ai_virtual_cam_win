"""Choose the inference implementation for a model: Core ML (macOS) or ONNX Runtime (Windows / .onnx).

The engine, model import and benchmark ask this module for a detector instead of naming Core ML classes,
so the same code runs on both platforms. The format follows the model file: .onnx -> ONNX Runtime,
.mlpackage / .mlmodel -> Core ML. Faces: Apple Vision on macOS, YuNet (OpenCV) elsewhere.
"""
from __future__ import annotations

from vcstudio.platform import IS_MAC


def is_onnx(info) -> bool:
    return str(info.package).lower().endswith(".onnx")


def make_detector(loaded):
    """Detector (or segmentation detector) for a LoadedModel; picks the class from task and format."""
    if is_onnx(loaded.info):
        from vcstudio.ai.onnx_engine import OnnxDetector, OnnxSegmentationDetector, OnnxYoloxDetector
        if loaded.info.task == "segment":
            return OnnxSegmentationDetector(loaded)
        return OnnxYoloxDetector(loaded) if loaded.info.format == "onnx-yolox" else OnnxDetector(loaded)
    from vcstudio.ai.coreml_engine import CoreMLDetector
    from vcstudio.ai.segmentation import SegmentationDetector
    return SegmentationDetector(loaded) if loaded.info.task == "segment" else CoreMLDetector(loaded)


def make_face_detector():
    if IS_MAC:
        from vcstudio.ai.coreml_engine import VisionFaceDetector
        return VisionFaceDetector()
    from vcstudio.ai.onnx_engine import YuNetFaceDetector
    return YuNetFaceDetector()


def face_detector_name() -> str:
    return "Apple Vision" if IS_MAC else "YuNet (OpenCV)"


def model_extensions() -> tuple[str, ...]:
    """Model files this platform runs (for discovery and the import dialog)."""
    return (".mlpackage", ".mlmodel", ".onnx") if IS_MAC else (".onnx",)
