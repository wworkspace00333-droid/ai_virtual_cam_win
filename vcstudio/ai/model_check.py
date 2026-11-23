"""Compatibility check for any ONNX model (0.6.0 Phase 1; extends vcstudio/ai/model_import.py).

model_import.py installs YOLO detectors and YOLO-seg models, the two layouts the detection pipeline runs. This module
checks a model of any category before it is used anywhere: it reads the interface, validates it against what the
category needs, runs a sample inference on every execution provider this PC has (the provider ONNX Runtime really
used is recorded, not the one asked for), measures warm-up and latency, and writes a compatibility report.

Nothing in an ONNX file is executed as code: ONNX Runtime only loads operators it ships with (no custom-op libraries
are registered), the file is hashed (SHA-256) and copied, never unpacked or run otherwise.

Categories and what "fits" means (checked on the declared shapes, then on the shapes the sample run returns):
  object_detection        one image input; a (1, 4+C, N), (1, N, 6) or (1, N, 5+C) output
  instance_segmentation   + a (1, M, H, W) prototype output
  semantic_segmentation   a (1, C, H, W) or (1, H, W) output, H and W >= 16
  background_segmentation same as semantic segmentation
  face_detection          one image input; at least one output with >= 4 values per row
  face_landmarks          an output with >= 5 points (x, y[, z])
  body_pose               an output with >= 17 points
  hand_landmarks          an output with >= 21 points
  ocr                     text detection (1, 1, H, W) or (H, W) map, or recognition (T, 1, C) / (1, T, C) sequence

A model that passes is registered in <app data>/models/custom/ with its report and an enabled flag. Using a custom
model in place of a built-in one is per module and only for the layouts that module decodes (detectors and YOLO-seg
through model_import; the others are checked, benchmarked and kept, and the report says what would use them).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

CATEGORIES: dict[str, str] = {
    "object_detection": "Object detection",
    "instance_segmentation": "Instance segmentation",
    "semantic_segmentation": "Semantic segmentation",
    "background_segmentation": "Background segmentation",
    "face_detection": "Face detection",
    "face_landmarks": "Face landmarks",
    "body_pose": "Body pose",
    "hand_landmarks": "Hand landmarks",
    "ocr": "OCR (text detection / recognition)",
}
MIN_POINTS = {"face_landmarks": 5, "body_pose": 17, "hand_landmarks": 21}
MAX_BYTES = 2 * 2**30                      # larger files need external data handling we do not do here
DEFAULT_SIDE = 320                         # dynamic spatial dimensions are tried at this size


class ModelCheckError(ValueError):
    pass


@dataclass
class ProviderResult:
    requested: str
    used: str = ""
    ok: bool = False
    warmup_ms: float = 0.0
    median_ms: float = 0.0
    p95_ms: float = 0.0
    error: str = ""


@dataclass
class CompatReport:
    source: str
    category: str
    sha256: str
    size_bytes: int
    inputs: list[dict] = field(default_factory=list)       # name, shape (None = dynamic), type
    outputs: list[dict] = field(default_factory=list)
    run_shapes: dict[str, list[int]] = field(default_factory=dict)   # output shapes of the sample run
    dynamic: bool = False
    producer: str = ""
    license: str = ""
    labels: list[str] = field(default_factory=list)
    fits: bool = False
    problems: list[str] = field(default_factory=list)
    providers: list[ProviderResult] = field(default_factory=list)
    checked: str = ""
    used_by: str = ""                                      # which module can run it, or "" (checked only)

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def best(self) -> ProviderResult | None:
        ok = [p for p in self.providers if p.ok]
        return min(ok, key=lambda p: p.median_ms) if ok else None


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _dims(shape) -> list[int | None]:
    return [d if isinstance(d, int) and d > 0 else None for d in shape]


def _feed(inp, side: int) -> np.ndarray:
    """A deterministic sample tensor for one input (image-like for 4-D inputs: a smooth gradient, not noise)."""
    shape = _dims(inp.shape)
    if len(shape) == 4:
        nchw = shape[1] in (1, 3, 4) or (shape[1] is None and shape[3] not in (1, 3, 4))
        n = 1
        c = shape[1] if nchw else shape[3]
        c = c or 3
        h = (shape[2] if nchw else shape[1]) or side
        w = (shape[3] if nchw else shape[2]) or side
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        img = np.stack([(xx / max(1, w - 1) + k * 0.1) % 1.0 for k in range(c)], 0) * 0.5 + (yy / max(1, h - 1))[None] * 0.5
        arr = img[None] if nchw else img.transpose(1, 2, 0)[None]
        arr = np.broadcast_to(arr, (n,) + arr.shape[1:])
    else:
        arr = np.full([d or 1 for d in shape], 0.5, np.float32)
    t = str(inp.type)
    if "uint8" in t:
        return np.ascontiguousarray((arr * 255).astype(np.uint8))
    if "int64" in t:
        return np.ascontiguousarray(arr.astype(np.int64))
    if "int32" in t:
        return np.ascontiguousarray(arr.astype(np.int32))
    if "float16" in t:
        return np.ascontiguousarray(arr.astype(np.float16))
    return np.ascontiguousarray(arr.astype(np.float32))


def validate(category: str, inputs: list[dict], shapes: list[list[int]]) -> tuple[bool, list[str], str]:
    """(fits, problems, used_by) from the input list and the output shapes of a real run. Pure: tested directly."""
    p: list[str] = []
    used_by = ""
    images = [i for i in inputs if len(i["shape"]) == 4]
    if category not in CATEGORIES:
        return False, [f"unknown category {category!r}"], ""
    if category != "ocr" and not images:
        p.append("no image input (a 4-D tensor N x C x H x W or N x H x W x C)")
    three = [s for s in shapes if len(s) == 3]
    four = [s for s in shapes if len(s) == 4]
    if category in ("object_detection", "instance_segmentation"):
        det = [s for s in three if s[1] >= 5 or s[2] >= 5]
        if not det:
            p.append("no detection output of shape (1, 4+C, N), (1, N, 6) or (1, N, 5+C)")
        if category == "instance_segmentation":
            if not four:
                p.append("no mask prototype output (1, M, H, W)")
            else:
                used_by = "detection pipeline (YOLO-seg layout, import through AI Engine → Import model)"
        else:
            used_by = "detection pipeline (YOLO layout, import through AI Engine → Import model)"
    elif category in ("semantic_segmentation", "background_segmentation"):
        maps = [s for s in shapes if len(s) in (3, 4) and s[-1] >= 16 and s[-2] >= 16]
        if not maps:
            p.append("no mask output (1, C, H, W) or (1, H, W) with H, W >= 16")
        elif category == "background_segmentation":
            used_by = "background (PP-HumanSeg layout: 2-class map)" if any(
                len(s) == 4 and s[1] == 2 for s in maps) else ""
    elif category == "face_detection":
        if not any(s and s[-1] >= 4 for s in shapes):
            p.append("no output with at least 4 values per row (a box)")
    elif category in MIN_POINTS:
        need = MIN_POINTS[category]
        best = 0
        for s in shapes:                     # (1, K*d) flat or (1, K, d), d = coordinates (+ visibility/presence)
            if len(s) == 2:
                for k in (5, 4, 3, 2):
                    if s[1] % k == 0 and need <= s[1] // k <= 600:
                        best = max(best, s[1] // k)
            elif len(s) == 3 and 2 <= s[2] <= 5 and s[1] <= 600:
                best = max(best, s[1])
        if best < need:
            p.append(f"no landmark output with at least {need} points ((1, K*d) or (1, K, d), d = 2..5)")
        else:                                # the MediaPipe layouts the app's analysers decode
            nhwc = any(i["shape"][1:] in ([256, 256, 3], [224, 224, 3]) for i in images)
            if category == "body_pose" and nhwc and any(s == [1, 195] for s in shapes) and \
                    any(i["shape"][1:] == [256, 256, 3] for i in images):
                used_by = "pose landmarks (MediaPipe pose layout: 256x256 input, 39 x 5 output)"
            elif category == "hand_landmarks" and nhwc and any(s == [1, 63] for s in shapes) and \
                    any(i["shape"][1:] == [224, 224, 3] for i in images):
                used_by = "hand landmarks (MediaPipe hand layout: 224x224 input, 21 x 3 output)"
    elif category == "ocr":
        det_map = [s for s in four if s[1] == 1 and s[2] >= 16 and s[3] >= 16]
        det_map += [s for s in shapes if len(s) == 2 and s[0] >= 16 and s[1] >= 16]
        seq = [s for s in three if max(s[0], s[1]) >= 4 and s[2] >= 10]
        if not det_map and not seq:
            p.append("neither a text probability map (1, 1, H, W) nor a character sequence (T, 1, C)")
        elif det_map:
            used_by = "privacy shield text regions (PP-OCR DB layout)"
    return not p, p, used_by if not p else ""


def check(src: Path, category: str, providers: list[str] | None = None, runs: int = 10,
          side: int = DEFAULT_SIDE) -> CompatReport:
    """Read, validate and run `src` on each provider; raise ModelCheckError only if it cannot be read at all."""
    import onnxruntime as ort
    src = Path(src)
    if not src.is_file() or src.suffix.lower() != ".onnx":
        raise ModelCheckError("choose an .onnx file")
    size = src.stat().st_size
    if size > MAX_BYTES:
        raise ModelCheckError(f"{size / 2**30:.1f} GB: models over 2 GB are not supported")
    rep = CompatReport(str(src), category, sha256_file(src), size,
                       checked=_dt.datetime.now().isoformat(timespec="seconds"))
    avail = list(ort.get_available_providers())
    want = [p for p in (providers or avail) if p in avail and p not in ("TensorrtExecutionProvider",)]
    if "CPUExecutionProvider" not in want:
        want.append("CPUExecutionProvider")
    first = True
    for prov in want:
        pr = ProviderResult(prov)
        rep.providers.append(pr)
        try:
            so = ort.SessionOptions()
            so.log_severity_level = 3
            if prov == "DmlExecutionProvider":
                so.enable_mem_pattern = False
                so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            plist = [prov] if prov == "CPUExecutionProvider" else [prov, "CPUExecutionProvider"]
            sess = ort.InferenceSession(str(src), sess_options=so, providers=plist)
            pr.used = sess.get_providers()[0]
            ins = sess.get_inputs()
            if first:
                meta = sess.get_modelmeta()
                props = dict(meta.custom_metadata_map or {})
                rep.producer = meta.producer_name or ""
                rep.license = str(props.get("license", ""))
                names = props.get("names") or props.get("labels") or ""
                if names:
                    import ast
                    try:
                        d = ast.literal_eval(str(names))
                        rep.labels = [str(d[k]) for k in sorted(d, key=int)] if isinstance(d, dict) else [str(x) for x in d]
                    except (ValueError, SyntaxError, TypeError):
                        rep.labels = [s.strip() for s in str(names).split(",") if s.strip()]
                rep.inputs = [{"name": i.name, "shape": _dims(i.shape), "type": i.type} for i in ins]
                rep.outputs = [{"name": o.name, "shape": _dims(o.shape), "type": o.type} for o in sess.get_outputs()]
                rep.dynamic = any(d is None for i in rep.inputs + rep.outputs for d in i["shape"])
            feed = {i.name: _feed(i, side) for i in ins}
            t0 = time.perf_counter()
            outs = sess.run(None, feed)
            pr.warmup_ms = round((time.perf_counter() - t0) * 1000, 2)
            if first:
                rep.run_shapes = {o.name: list(np.asarray(v).shape) for o, v in zip(sess.get_outputs(), outs)}
            ms = []
            deadline = time.perf_counter() + 20
            for _ in range(max(1, runs)):
                t0 = time.perf_counter()
                sess.run(None, feed)
                ms.append((time.perf_counter() - t0) * 1000)
                if time.perf_counter() > deadline:
                    break
            ms.sort()
            pr.median_ms = round(ms[len(ms) // 2], 2)
            pr.p95_ms = round(ms[int(0.95 * (len(ms) - 1))], 2)
            pr.ok = True
            first = False
        except Exception as e:                               # noqa: BLE001 - recorded in the report
            pr.error = f"{type(e).__name__}: {e}"[:400]
    if not any(p.ok for p in rep.providers):
        rep.problems.append("the model does not run on any execution provider: " +
                            "; ".join(p.error for p in rep.providers if p.error)[:600])
        return rep
    fits, problems, used_by = validate(category, rep.inputs, list(rep.run_shapes.values()))
    rep.fits, rep.used_by = fits, used_by
    rep.problems += problems
    return rep


# ----------------------------------------------------------------------------------------------- registry
def custom_dir(models_dir: Path) -> Path:
    return Path(models_dir) / "custom"


def register(rep: CompatReport, models_dir: Path, name: str) -> Path:
    """Copy a model that fits into <models>/custom/<name>.onnx with <name>.model.json (report + enabled flag)."""
    if not rep.fits:
        raise ModelCheckError("the model did not pass the check: " + "; ".join(rep.problems))
    clean = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in name.strip()).strip("-.")[:60]
    if not clean:
        raise ModelCheckError("give the model a name")
    d = custom_dir(models_dir)
    d.mkdir(parents=True, exist_ok=True)
    dest = d / f"{clean}.onnx"
    if dest.exists():
        raise ModelCheckError(f"a custom model named “{clean}” already exists")
    part = dest.with_suffix(".part")
    shutil.copy2(rep.source, part)
    if sha256_file(part) != rep.sha256:                   # changed between the check and the copy
        part.unlink(missing_ok=True)
        raise ModelCheckError("the file changed after it was checked; check it again")
    part.replace(dest)
    meta = {"name": clean, "category": rep.category, "enabled": True, "report": rep.as_dict()}
    meta["report"]["source"] = Path(rep.source).name
    (d / f"{clean}.model.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    log.info("custom model registered: %s (%s, sha256 %s)", clean, rep.category, rep.sha256[:12])
    return dest


def list_custom(models_dir: Path) -> list[dict]:
    """Registered custom models whose file is present and still has the recorded SHA-256 size (cheap check)."""
    out = []
    d = custom_dir(models_dir)
    for meta_file in sorted(d.glob("*.model.json")) if d.exists() else []:
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        f = d / f"{meta.get('name', '')}.onnx"
        meta["present"] = f.exists() and f.stat().st_size == meta.get("report", {}).get("size_bytes")
        out.append(meta)
    return out


def set_enabled(models_dir: Path, name: str, enabled: bool) -> None:
    p = custom_dir(models_dir) / f"{name}.model.json"
    meta = json.loads(p.read_text(encoding="utf-8"))
    meta["enabled"] = bool(enabled)
    p.write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")


def verify(models_dir: Path, name: str) -> bool:
    """Full SHA-256 check of a registered model against its report (before a module loads it)."""
    d = custom_dir(models_dir)
    meta = json.loads((d / f"{name}.model.json").read_text(encoding="utf-8"))
    f = d / f"{name}.onnx"
    return f.exists() and sha256_file(f) == meta["report"]["sha256"]


ROLES = {"background": "background", "text": "privacy shield text", "pose": "pose landmarks",
         "hands": "hand landmarks"}           # role -> the used_by prefix it needs


def usable_for(meta: dict) -> str:
    """The role a registered model can take ("background" / "text"), or "" (pure: tested)."""
    used = str(meta.get("report", {}).get("used_by", ""))
    return next((role for role, prefix in ROLES.items() if used.startswith(prefix)), "")


def active_path(models_dir: Path, role: str, chosen: str) -> Path | None:
    """The file of the custom model chosen for `role` if it is registered, enabled, fits the role and still has its
    recorded SHA-256; None otherwise (the built-in model is used)."""
    if not chosen:
        return None
    d = custom_dir(models_dir)
    try:
        meta = json.loads((d / f"{chosen}.model.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not meta.get("enabled") or usable_for(meta) != role or not verify(models_dir, chosen):
        log.warning("custom model %s not used for %s (disabled, changed or not fitting)", chosen, role)
        return None
    return d / f"{chosen}.onnx"


def unregister(models_dir: Path, name: str) -> None:
    d = custom_dir(models_dir)
    (d / f"{name}.onnx").unlink(missing_ok=True)
    (d / f"{name}.model.json").unlink(missing_ok=True)
