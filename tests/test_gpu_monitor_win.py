"""GPU counters (PDH) and the GDI screenshot on Windows.

The PDH tests use a stand-in for pdh.dll that answers PdhGetFormattedCounterArrayW the way Windows does (size query,
then an array of PDH_FMT_COUNTERVALUE_ITEM_W), so the parsing and Task Manager's "busiest engine type" rule are
checked on any machine, also one without a GPU driver (this server, the CI runner)."""
from __future__ import annotations

import ctypes
import sys

import numpy as np
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows PDH / GDI")


class FakePdh:
    """Minimal pdh.dll: counters 1 (engine) and 2 (memory), each with a list of (instance, value, status)."""

    def __init__(self, engine=None, memory=None, open_rc=0, add_ok=(True, True), collect_rc=0):
        self.items = {1: engine or [], 2: memory or []}
        self.open_rc, self.add_ok, self.collect_rc = open_rc, add_ok, collect_rc
        self.closed = 0
        self._keep = []                          # name strings must outlive the buffer

    def PdhOpenQueryW(self, src, user, ref):
        ref._obj.value = 7
        return self.open_rc

    def PdhAddEnglishCounterW(self, q, path, user, ref):
        from vcstudio.platform.win.gpu_monitor import GpuMonitor
        idx = 1 if path == GpuMonitor.ENGINE else 2
        if not self.add_ok[idx - 1]:
            return 0xC0000BB8                     # PDH_CSTATUS_NO_OBJECT
        ref._obj.value = idx
        return 0

    def PdhCollectQueryData(self, q):
        return self.collect_rc

    def PdhCloseQuery(self, q):
        self.closed += 1
        return 0

    def PdhGetFormattedCounterArrayW(self, counter, fmt, size_ref, count_ref, buf):
        from vcstudio.platform.win import gpu_monitor as gm
        rows = self.items[counter.value]
        need = ctypes.sizeof(gm._FmtItem) * len(rows)
        if buf is None:
            size_ref._obj.value, count_ref._obj.value = need, len(rows)
            return gm.PDH_MORE_DATA if rows else 0
        arr = ctypes.cast(buf, ctypes.POINTER(gm._FmtItem))
        for i, (name, value, status) in enumerate(rows):
            s = ctypes.c_wchar_p(name)
            self._keep.append(s)
            arr[i].szName = s
            arr[i].FmtValue.CStatus = status
            if fmt & gm.PDH_FMT_DOUBLE:
                arr[i].FmtValue.u.doubleValue = value
            else:
                arr[i].FmtValue.u.largeValue = int(value)
        count_ref._obj.value = len(rows)
        return 0


@pytest.fixture
def monitor(monkeypatch):
    from vcstudio.platform.win import gpu_monitor as gm

    def make(pdh):
        monkeypatch.setattr(gm, "_load_pdh", lambda: pdh)
        return gm.GpuMonitor()
    return make


def _eng(pid, etype, v, status=0):
    return (f"pid_{pid}_luid_0x0_0x1_phys_0_eng_0_engtype_{etype}", v, status)


def test_busiest_engine_type_and_dedicated_memory(monitor):
    pdh = FakePdh(engine=[_eng(1, "3D", 30.0), _eng(2, "3D", 25.5), _eng(1, "VideoDecode", 40.0),
                          _eng(3, "Copy", 99.0, status=2)],          # status 2 = invalid data: ignored
                  memory=[("luid_0x0_0x1_phys_0", 512 * 2**20, 0), ("luid_0x0_0x2_phys_0", 2**20, 1)])
    m = monitor(pdh)
    assert m.available and m.error == ""
    util, mem = m.sample()
    assert util == pytest.approx(55.5)                                 # 3D: 30 + 25.5 beats video decode 40
    assert mem == 513 * 2**20
    m.close()
    assert pdh.closed == 1 and not m.available and m.sample() == (None, None)


def test_utilisation_is_capped_and_unnamed_engines_count_as_other(monitor):
    m = monitor(FakePdh(engine=[_eng(1, "3D", 80.0), _eng(2, "3D", 70.0), ("weird", 5.0, 0)]))
    assert m.sample() == (100.0, None)


def test_no_values_yet_and_failed_collect(monitor):
    pdh = FakePdh()
    m = monitor(pdh)
    assert m.sample() == (None, None)                                  # counters exist, no instances yet
    pdh.collect_rc = 1
    assert m.sample() == (None, None)


def test_counters_missing_or_query_failing_report_why(monitor, monkeypatch):
    m = monitor(FakePdh(add_ok=(False, False)))
    assert not m.available and "not available" in m.error
    m = monitor(FakePdh(add_ok=(False, True), memory=[("luid", 1024, 0)]))
    assert m.available and m.sample() == (None, 1024)                  # memory without the engine counter
    m = monitor(FakePdh(open_rc=5))
    assert not m.available and m.error == "PdhOpenQuery failed"

    from vcstudio.platform.win import gpu_monitor as gm

    def no_dll():
        raise OSError("pdh.dll missing")
    monkeypatch.setattr(gm, "_load_pdh", no_dll)
    m = gm.GpuMonitor()
    assert not m.available and "pdh.dll missing" in m.error


def test_read_gpu_stats_opens_one_monitor(monkeypatch):
    from vcstudio.platform.win import gpu_monitor as gm
    monkeypatch.setattr(gm, "_load_pdh", lambda: FakePdh(engine=[_eng(1, "3D", 12.0)]))
    monkeypatch.setattr(gm, "_monitor", None)
    assert gm.read_gpu_stats() == (12.0, None)
    first = gm._monitor
    assert gm.read_gpu_stats() == (12.0, None) and gm._monitor is first


def test_real_counters_never_make_up_a_value():
    """On this machine: either real numbers or None (no WDDM GPU driver), never an exception."""
    from vcstudio.platform.win.gpu_monitor import GpuMonitor
    m = GpuMonitor()
    try:
        util, mem = m.sample()
        assert util is None or 0.0 <= util <= 100.0
        assert mem is None or mem >= 0
        assert m.available or m.error
    finally:
        m.close()


def test_primary_screen_matches_the_display_size():
    from vcstudio.platform.win.screenshot import SM_CXSCREEN, SM_CYSCREEN, primary_screen
    try:
        img = primary_screen()
    except RuntimeError as e:                                          # service session without a desktop
        pytest.skip(f"no interactive desktop: {e}")
    u32 = ctypes.windll.user32
    assert img.dtype == np.uint8 and img.shape == (u32.GetSystemMetrics(SM_CYSCREEN), u32.GetSystemMetrics(SM_CXSCREEN), 3)
    assert img.flags.c_contiguous
