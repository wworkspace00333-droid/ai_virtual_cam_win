"""What this PC can do, in one place (audit 2026-11-19, hardware compatibility layer step 1).

`probe()` collects the facts the AI and pipeline choices depend on — hardware GPUs and their video memory, CPU cores,
RAM, the ONNX Runtime providers that can actually help — and classifies the machine into a tier. It only reads
(DXGI adapter list, psutil, ONNX Runtime's provider list): no model is loaded, nothing is changed. The profile is
written to the start-up log and the diagnostics ZIP, so a report from a user's PC says what the app saw.

Tiers (what to expect, used for messages and limits):
  gpu           a hardware GPU with >= 2 GB dedicated video memory and DirectML (or CUDA) available
  gpu-low-vram  a hardware GPU with less than 2 GB dedicated memory (integrated graphics usually) and DirectML
  cpu           no usable GPU for AI, >= 4 physical cores
  cpu-low       no usable GPU for AI, fewer than 4 physical cores (the development server: 2)
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field

log = logging.getLogger(__name__)

LOW_VRAM_MB = 2048
FEW_CORES = 4
OPEN_VOCAB_MIN_RAM_GB = 8.0      # OWLv2 + SAM grew the process by ~4 GB on the CPU


@dataclass
class HardwareProfile:
    gpus: list[dict] = field(default_factory=list)      # hardware adapters: name, vendor, dedicated/shared MB
    best_gpu: str = ""
    best_gpu_vram_mb: int = 0
    physical_cores: int = 0
    logical_cores: int = 0
    ram_total_gb: float = 0.0
    ram_free_gb: float = 0.0
    providers: list[str] = field(default_factory=list)  # ONNX Runtime providers in the package
    gpu_ai: str = ""                                     # "DirectML" / "CUDA" when a hardware GPU can run models
    tier: str = "cpu-low"
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        gpu = f"{self.best_gpu} ({self.best_gpu_vram_mb} MB)" if self.best_gpu else "no hardware GPU"
        return (f"{self.tier}: {gpu}; AI on {self.gpu_ai or 'CPU'}; {self.physical_cores} cores / "
                f"{self.logical_cores} threads; RAM {self.ram_total_gb:.1f} GB ({self.ram_free_gb:.1f} GB free)")

    @property
    def open_vocab_practical(self) -> bool:
        """'Any object by text' at a usable speed needs a GPU; on the CPU it takes seconds per frame."""
        return bool(self.gpu_ai) and self.ram_total_gb >= OPEN_VOCAB_MIN_RAM_GB


def classify(gpus: list[dict], providers: list[str], physical: int, ram_total_gb: float) -> tuple[str, str, list[str]]:
    """(tier, GPU AI provider label, notes) from the raw facts; pure, so it is tested with made-up machines."""
    notes = []
    hw = [g for g in gpus if not g.get("software") and g.get("vendor_id") != 0x1414]   # 0x1414: Microsoft (WARP)
    best = max(hw, key=lambda g: g.get("dedicated_vram_mb", 0), default=None)
    gpu_ai = ""
    if best is not None:
        if "CUDAExecutionProvider" in providers and best.get("vendor") == "NVIDIA":
            gpu_ai = "CUDA"
        elif "DmlExecutionProvider" in providers:
            gpu_ai = "DirectML"
        else:
            notes.append("hardware GPU present, but the ONNX Runtime package has no GPU provider")
    if gpu_ai and best is not None:
        tier = "gpu" if best.get("dedicated_vram_mb", 0) >= LOW_VRAM_MB else "gpu-low-vram"
        if tier == "gpu-low-vram":
            notes.append("little dedicated video memory: models share system memory with the GPU")
    else:
        tier = "cpu" if physical >= FEW_CORES else "cpu-low"
        notes.append("AI runs on the CPU: smaller models and lower AI rates; 'any object by text' is not real-time")
    if ram_total_gb and ram_total_gb < OPEN_VOCAB_MIN_RAM_GB:
        notes.append(f"{ram_total_gb:.0f} GB RAM: 'any object by text' will not have room")
    return tier, gpu_ai, notes


def probe() -> HardwareProfile:
    """Read the machine (fast: DXGI adapter list, psutil, provider names). Never raises."""
    p = HardwareProfile()
    try:
        import psutil
        p.physical_cores = psutil.cpu_count(logical=False) or 0
        p.logical_cores = psutil.cpu_count() or 0
        vm = psutil.virtual_memory()
        p.ram_total_gb, p.ram_free_gb = round(vm.total / 2**30, 1), round(vm.available / 2**30, 1)
    except Exception as e:                       # psutil missing or failing: keep zeros
        p.notes.append(f"cpu/memory unknown: {e}")
    try:
        import onnxruntime as ort
        p.providers = list(ort.get_available_providers())
    except Exception as e:
        p.notes.append(f"ONNX Runtime unavailable: {e}")
    gpus: list[dict] = []
    try:
        from vcstudio.platform import IS_WINDOWS
        if IS_WINDOWS:
            from vcstudio.platform.win.sysinfo import hardware_gpus
            gpus = hardware_gpus()               # without WARP / Basic Render Driver (Microsoft, not flagged software)
    except Exception as e:
        p.notes.append(f"GPU list unavailable: {e}")
    p.gpus = [{k: g.get(k) for k in ("name", "vendor", "dedicated_vram_mb", "shared_memory_mb")} for g in gpus]
    if gpus:
        best = max(gpus, key=lambda g: g.get("dedicated_vram_mb", 0))
        p.best_gpu, p.best_gpu_vram_mb = str(best.get("name", "")), int(best.get("dedicated_vram_mb", 0))
    p.tier, p.gpu_ai, notes = classify(gpus, p.providers, p.physical_cores, p.ram_total_gb)
    p.notes += notes
    return p


_cached: HardwareProfile | None = None


def profile(refresh: bool = False) -> HardwareProfile:
    """The profile of this PC, probed once per process (RAM free is as of that moment)."""
    global _cached
    if _cached is None or refresh:
        _cached = probe()
    return _cached
