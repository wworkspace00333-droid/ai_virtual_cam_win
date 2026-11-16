"""Model registry: discovery, compilation cache, loading with compute-unit selection.

Models live in <repo>/models (bundled) and <app data>/models (user-added), each with a sibling
`<name>.json` (see scripts/convert_models.py).
  macOS:   `<name>.mlpackage` / `.mlmodel` (Core ML); compiled `.mlmodelc` bundles are cached under
           <app data>/models/compiled/ keyed by package mtime.
  Windows: `<name>.onnx` loaded into an ONNX Runtime session (vcstudio/ai/onnx_engine.py). An .onnx
           file without its .json (e.g. the YuNet face model) is not a detector and is not listed.
"""
from __future__ import annotations

import json
import logging
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from vcstudio.core.resources import models_dir

log = logging.getLogger(__name__)

REPO_MODELS = models_dir()          # bundled models (repository models/ in development)

COMPUTE_UNITS = {
    "all": "CPU + GPU + Neural Engine",
    "cpu_and_ne": "CPU + Neural Engine",
    "cpu_and_gpu": "CPU + GPU",
    "cpu_only": "CPU only",
}
from vcstudio.platform import IS_MAC as _IS_MAC  # noqa: E402

if not _IS_MAC:
    # Same keys (profiles stay portable); meaning on Windows: see vcstudio/ai/onnx_engine.provider_plan.
    # "cpu_and_ne" (an old macOS profile) keeps working there and means the same as "all".
    COMPUTE_UNITS = {
        "all": "Automatic (GPU if available, else CPU)",
        "cpu_and_gpu": "GPU (DirectML)",
        "cpu_only": "CPU only",
    }
    COMPUTE_UNITS_HIDDEN = {"cpu_and_ne": "Automatic (GPU if available, else CPU)"}
else:
    COMPUTE_UNITS_HIDDEN = {}


def _cu_constant(key: str):
    import CoreML  # type: ignore
    return {
        "all": CoreML.MLComputeUnitsAll,
        "cpu_and_ne": CoreML.MLComputeUnitsCPUAndNeuralEngine,
        "cpu_and_gpu": CoreML.MLComputeUnitsCPUAndGPU,
        "cpu_only": CoreML.MLComputeUnitsCPUOnly,
    }[key]


class ModelError(RuntimeError):
    pass


@dataclass
class ModelInfo:
    name: str
    package: Path
    task: str = "detect"
    imgsz: int = 640
    classes: list[str] = field(default_factory=list)
    source: str = ""
    license: str = ""
    size_mb: float = 0.0
    origin: str = "builtin"            # builtin | user (imported, vcstudio/ai/model_import.py)
    confidence: float | None = None    # suggested detection threshold for this model
    source_classes: list[str] = field(default_factory=list)   # the model's own names (before class_map)
    benchmark: dict = field(default_factory=dict)
    format: str = ""                   # output layout, e.g. onnx-raw-detect (YOLO), onnx-yolox (vcstudio/ai/backend.py)

    @classmethod
    def from_package(cls, pkg: Path) -> "ModelInfo":
        meta_path = pkg.with_suffix(".json")
        meta = json.loads(meta_path.read_text(encoding="utf-8-sig")) if meta_path.exists() else {}
        files = [pkg] if pkg.is_file() else [f for f in pkg.rglob("*") if f.is_file()]
        size = sum(f.stat().st_size for f in files) / 2**20
        classes = list(meta.get("classes", []))
        return cls(name=pkg.stem, package=pkg, task=meta.get("task", "detect"), imgsz=meta.get("imgsz", 640),
                   classes=classes, source=meta.get("source", ""),
                   license=meta.get("license", ""), size_mb=round(size, 1),
                   origin="user" if meta.get("origin") == "user" else "builtin",
                   confidence=meta.get("confidence"),
                   source_classes=list(meta.get("source_classes") or classes),
                   benchmark=dict(meta.get("benchmark") or {}), format=str(meta.get("format", "")))

    def label_map(self) -> dict[str, str]:
        """The model's own class name -> the name shown and used by regions (identity unless remapped)."""
        return dict(zip(self.source_classes, self.classes)) if len(self.source_classes) == len(self.classes) else {}


@dataclass
class LoadedModel:
    info: ModelInfo
    mlmodel: object             # Core ML MLModel, or onnxruntime.InferenceSession for .onnx models
    compute_units: str
    load_seconds: float
    backend: str = ""           # what actually runs it, e.g. "DirectML (NVIDIA ...)", "CPU", "Core ML"


class ModelManager:
    def __init__(self, user_models_dir: Path, extra_dirs: list[Path] | None = None):
        self.user_dir = user_models_dir
        self.dirs = [REPO_MODELS, user_models_dir] + list(extra_dirs or [])
        self.cache_dir = user_models_dir / "compiled"
        self._lock = threading.Lock()
        self._loaded: dict[str, LoadedModel] = {}

    def available(self, max_age_s: float = 5.0) -> dict[str, ModelInfo]:
        """Installed models (directory scan cached for `max_age_s`; it was measured on the hot path)."""
        now = time.monotonic()
        cached = getattr(self, "_avail_cache", None)
        if cached and now - cached[0] < max_age_s:
            return dict(cached[1])
        out: dict[str, ModelInfo] = {}
        from vcstudio.ai.backend import model_extensions
        exts = model_extensions()
        for d in self.dirs:
            if d.is_dir():
                pkgs = [p for ext in exts for p in d.glob(f"*{ext}")]
                for pkg in sorted(pkgs):
                    if pkg.suffix == ".onnx" and not pkg.with_suffix(".json").exists():
                        continue                     # not a detector (e.g. the YuNet face model)
                    try:
                        out.setdefault(pkg.stem, ModelInfo.from_package(pkg))
                    except Exception as e:
                        log.warning("Ignoring model %s: %s", pkg, e)
        self._avail_cache = (now, out)
        return dict(out)

    def resolve(self, name: str, task: str = "detect", prefer_large: bool = False) -> ModelInfo:
        models = {k: v for k, v in self.available().items() if v.task == task}
        if not models:
            raise ModelError(f"No {task} models found. Run: " + (".venv-convert/bin/python scripts/convert_models.py"
                             if _IS_MAC else r"C:\vcs-convert\Scripts\python scripts\convert_models.py --format onnx"))
        if name in ("auto", "", None):
            # "auto" picks by size among the built-in COCO models only: an imported model may detect
            # entirely different classes, so it is used only when chosen by name.
            pool = [m for m in models.values() if m.origin == "builtin"] or list(models.values())
            ranked = sorted(pool, key=lambda m: m.size_mb)
            return ranked[-1] if prefer_large else ranked[0]
        if name not in models:
            raise ModelError(f"Model {name!r} not found (available: {', '.join(sorted(models))})")
        return models[name]

    def ranked(self, task: str = "detect", include_user: bool = False) -> list[ModelInfo]:
        """Models of a task from largest (highest quality) to smallest; built-in only unless include_user."""
        return sorted((m for m in self.available().values()
                       if m.task == task and (include_user or m.origin == "builtin")), key=lambda m: -m.size_mb)

    def builtin_classes(self) -> list[str]:
        """Class names of the built-in detectors (all COCO-80; YOLO11 or YOLOX, whichever is installed)."""
        for m in sorted(self.available().values(), key=lambda m: m.size_mb):
            if m.task == "detect" and m.origin == "builtin" and m.classes:
                return list(m.classes)
        return []

    def invalidate(self) -> None:
        """Forget the directory scan (after a model was imported or removed)."""
        self._avail_cache = None

    def _compiled_path(self, info: ModelInfo) -> Path:
        import CoreML  # type: ignore
        import Foundation  # type: ignore
        files = [info.package] if info.package.is_file() else [f for f in info.package.rglob("*") if f.is_file()]
        mtime = int(max(f.stat().st_mtime for f in files))
        target = self.cache_dir / f"{info.name}-{mtime}.mlmodelc"
        if target.exists():
            return target
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        t0 = time.perf_counter()
        url, err = CoreML.MLModel.compileModelAtURL_error_(Foundation.NSURL.fileURLWithPath_(str(info.package)), None)
        if url is None:
            raise ModelError(f"Core ML compile failed for {info.name}: {err}")
        for old in self.cache_dir.glob(f"{info.name}-*.mlmodelc"):
            shutil.rmtree(old, ignore_errors=True)
        shutil.move(str(url.path()), target)
        log.info("Compiled %s in %.2f s", info.name, time.perf_counter() - t0)
        return target

    def load(self, info: ModelInfo, compute_units: str = "all") -> LoadedModel:
        key = f"{info.name}:{compute_units}"
        from vcstudio.ai.backend import is_onnx
        if is_onnx(info):
            with self._lock:
                if key in self._loaded:
                    return self._loaded[key]
                from vcstudio.ai.onnx_engine import create_session
                t0 = time.perf_counter()
                try:
                    sess, backend = create_session(info.package, compute_units, cache_dir=self.cache_dir / "onnx")
                except Exception as e:
                    raise ModelError(f"ONNX Runtime load failed for {info.name}: {e}") from e
                lm = LoadedModel(info, sess, compute_units, time.perf_counter() - t0, backend)
                self._loaded[key] = lm
                log.info("Loaded %s on %s in %.2f s", info.name, backend, lm.load_seconds)
                return lm
        import CoreML  # type: ignore
        import Foundation  # type: ignore
        with self._lock:
            if key in self._loaded:
                return self._loaded[key]
            path = self._compiled_path(info)
            cfg = CoreML.MLModelConfiguration.alloc().init()
            cfg.setComputeUnits_(_cu_constant(compute_units))
            t0 = time.perf_counter()
            model, err = CoreML.MLModel.modelWithContentsOfURL_configuration_error_(
                Foundation.NSURL.fileURLWithPath_(str(path)), cfg, None)
            if model is None:
                raise ModelError(f"Core ML load failed for {info.name}: {err}")
            lm = LoadedModel(info, model, compute_units, time.perf_counter() - t0, f"Core ML ({COMPUTE_UNITS[compute_units]})")
            self._loaded[key] = lm
            log.info("Loaded %s (%s) in %.2f s", info.name, COMPUTE_UNITS[compute_units], lm.load_seconds)
            return lm

    def unload(self, name: str | None = None) -> None:
        """Drop references so Core ML can free memory (all models when name is None)."""
        with self._lock:
            for k in list(self._loaded):
                if name is None or k.split(":")[0] == name:
                    del self._loaded[k]

    def loaded(self) -> list[str]:
        with self._lock:
            return list(self._loaded)
