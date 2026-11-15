"""Windows platform layer: paths, clock, frames as arrays, system information, GPU counters."""
import os
import sys
import time

import numpy as np
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows platform layer")


def test_default_data_dir_is_local_appdata(monkeypatch):
    from vcstudio.core.paths import AppPaths
    monkeypatch.delenv("VCSTUDIO_HOME", raising=False)
    root = AppPaths.default().root
    assert root.name == "AIVirtualCameraStudio"
    assert str(root).lower().startswith(os.environ["LOCALAPPDATA"].lower())


def test_host_clock_is_high_resolution():
    """Frame timestamps need sub-millisecond resolution at 60 fps (time.monotonic ticks at ~15.6 ms on Windows)."""
    from vcstudio.core.frame import host_time_s
    deltas = []
    for _ in range(200):
        a = host_time_s()
        b = host_time_s()
        while b == a:
            b = host_time_s()
        deltas.append(b - a)
    assert min(deltas) < 1e-4
    t0, w0 = host_time_s(), time.perf_counter()
    time.sleep(0.05)
    assert abs((host_time_s() - t0) - (time.perf_counter() - w0)) < 0.005


def test_array_frames_are_their_own_pixel_buffer():
    from vcstudio.core.frame import Frame
    from vcstudio.core.pixelbuffer import pixel_buffer_for
    arr = np.zeros((4, 6, 4), np.uint8)
    arr[..., 2] = 200
    f = Frame(1, 0.0, 0.0, 6, 4, array=arr)
    pb = pixel_buffer_for(f)
    assert pb is arr                                  # no copy for contiguous frames
    out = Frame(1, 0.0, 0.0, 6, 4, pixel_buffer=pb)   # what the pipeline builds from it
    with out.bgra() as a:
        assert a.shape == (4, 6, 4) and a[0, 0, 2] == 200
    view = arr[:, ::2]                                # non-contiguous: made contiguous
    pb2 = pixel_buffer_for(Frame(2, 0.0, 0.0, 3, 4, array=view))
    assert pb2.flags.c_contiguous and np.array_equal(pb2, view)


def test_autorelease_pool_is_noop():
    from vcstudio.platform import autorelease_pool
    with autorelease_pool():
        pass


def test_system_information():
    from vcstudio.platform.win import sysinfo
    major, _, build = sysinfo.windows_build()
    assert major == 10 and build > 10000
    assert sysinfo.cpu_name()
    ads = sysinfo.display_adapters()
    assert ads and all({"name", "vendor", "dedicated_vram_mb", "software", "luid"} <= set(a) for a in ads)
    assert all(a not in sysinfo.hardware_gpus() for a in ads if a["software"])
    assert sysinfo.ui_language().isalpha()


def test_gpu_counters_report_or_say_unavailable():
    """Either real numbers or None; never an invented value."""
    from vcstudio.core.performance import read_gpu_stats
    read_gpu_stats()
    time.sleep(0.2)
    util, mem = read_gpu_stats()
    assert util is None or 0.0 <= util <= 100.0
    assert mem is None or mem >= 0


def test_footprint_is_private_bytes():
    from vcstudio.core.performance import phys_footprint
    fp = phys_footprint()
    assert isinstance(fp, int) and fp > 1_000_000


def test_system_language_detection():
    from vcstudio import i18n
    assert i18n.system_language() in i18n.LANGUAGES
