"""Hardware profile (audit 2026-11-19, compatibility layer step 1): tiers from made-up machines, and the real probe."""
from __future__ import annotations

import sys

import pytest

from vcstudio.core.hardware import HardwareProfile, classify, probe

DML = ["DmlExecutionProvider", "CPUExecutionProvider"]
NVIDIA = {"name": "NVIDIA GeForce RTX 4060", "vendor": "NVIDIA", "vendor_id": 0x10DE, "dedicated_vram_mb": 8188}
INTEL_IGPU = {"name": "Intel(R) UHD Graphics 770", "vendor": "Intel", "vendor_id": 0x8086, "dedicated_vram_mb": 128}
WARP = {"name": "Microsoft Basic Render Driver", "vendor": "0x1414", "vendor_id": 0x1414, "dedicated_vram_mb": 0}


@pytest.mark.parametrize("gpus, providers, cores, ram, tier, ai", [
    ([NVIDIA], DML, 8, 32.0, "gpu", "DirectML"),
    ([NVIDIA], ["CUDAExecutionProvider", "CPUExecutionProvider"], 8, 32.0, "gpu", "CUDA"),
    ([INTEL_IGPU], DML, 6, 16.0, "gpu-low-vram", "DirectML"),
    ([INTEL_IGPU, NVIDIA], DML, 8, 32.0, "gpu", "DirectML"),           # the adapter with more memory wins
    ([WARP], DML, 2, 31.4, "cpu-low", ""),                              # this server: WARP is not a GPU
    ([], DML, 8, 16.0, "cpu", ""),
    ([NVIDIA], ["CPUExecutionProvider"], 8, 32.0, "cpu", ""),           # a GPU but no GPU provider in the package
])
def test_tiers(gpus, providers, cores, ram, tier, ai):
    t, gpu_ai, notes = classify(gpus, providers, cores, ram)
    assert (t, gpu_ai) == (tier, ai)
    assert isinstance(notes, list)


def test_low_ram_note_and_open_vocab_needs_a_gpu():
    _, _, notes = classify([NVIDIA], DML, 4, 6.0)
    assert any("6 GB RAM" in n for n in notes)
    assert HardwareProfile(gpu_ai="DirectML", ram_total_gb=16).open_vocab_practical
    assert not HardwareProfile(gpu_ai="", ram_total_gb=64).open_vocab_practical
    assert not HardwareProfile(gpu_ai="DirectML", ram_total_gb=6).open_vocab_practical


@pytest.mark.skipif(sys.platform != "win32", reason="Windows adapter list")
def test_probe_reads_this_machine():
    p = probe()
    assert p.logical_cores >= p.physical_cores >= 1 and p.ram_total_gb > 0
    assert "CPUExecutionProvider" in p.providers
    assert all(g["vendor"] != "0x1414" for g in p.gpus)                  # WARP never listed as a GPU
    assert p.tier in ("gpu", "gpu-low-vram", "cpu", "cpu-low") and p.tier in p.summary()
    if not p.gpus:
        assert p.gpu_ai == "" and p.tier.startswith("cpu")
