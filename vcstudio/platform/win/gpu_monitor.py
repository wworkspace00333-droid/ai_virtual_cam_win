"""GPU utilisation and memory on Windows through Performance Data Helper (PDH) counters.

These are the counters Task Manager shows:
  \\GPU Engine(*)\\Utilization Percentage      per engine instance (pid_..._luid_..._engtype_3D, ...)
  \\GPU Adapter Memory(*)\\Dedicated Usage     per adapter, bytes
Utilisation = the busiest engine type summed over its instances (Task Manager's "GPU" column),
capped at 100. Rate counters need two samples, so the first read after opening returns None.
On machines without a WDDM GPU driver the counter set does not exist: the monitor reports None and
the UI shows "n/a" (never a made-up value).
"""
from __future__ import annotations

import ctypes
import logging
import threading
from collections import defaultdict
from ctypes import wintypes

log = logging.getLogger(__name__)

PDH_FMT_DOUBLE = 0x00000200
PDH_FMT_LARGE = 0x00000400
PDH_FMT_NOCAP100 = 0x00008000
PDH_MORE_DATA = 0x800007D2
ERROR_SUCCESS = 0


def _load_pdh():
    return ctypes.WinDLL("pdh")


class _FmtValue(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("longValue", wintypes.LONG), ("doubleValue", ctypes.c_double),
                    ("largeValue", ctypes.c_longlong), ("AnsiStringValue", ctypes.c_char_p),
                    ("WideStringValue", ctypes.c_wchar_p)]
    _fields_ = [("CStatus", wintypes.DWORD), ("u", _U)]


class _FmtItem(ctypes.Structure):
    _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", _FmtValue)]


class GpuMonitor:
    ENGINE = r"\GPU Engine(*)\Utilization Percentage"
    MEMORY = r"\GPU Adapter Memory(*)\Dedicated Usage"

    def __init__(self):
        self._lock = threading.Lock()
        self._query = None
        self._engine = None
        self._memory = None
        self.available = False
        self.error = ""
        try:
            self._pdh = _load_pdh()
        except OSError as e:
            self.error = str(e)
            return
        q = wintypes.HANDLE()
        if self._pdh.PdhOpenQueryW(None, None, ctypes.byref(q)) != ERROR_SUCCESS:
            self.error = "PdhOpenQuery failed"
            return
        self._query = q
        eng, mem = wintypes.HANDLE(), wintypes.HANDLE()
        add = self._pdh.PdhAddEnglishCounterW
        if add(q, self.ENGINE, None, ctypes.byref(eng)) == ERROR_SUCCESS:
            self._engine = eng
        if add(q, self.MEMORY, None, ctypes.byref(mem)) == ERROR_SUCCESS:
            self._memory = mem
        if self._engine is None and self._memory is None:
            self.error = "GPU performance counters are not available on this system"
            self.close()
            return
        self._pdh.PdhCollectQueryData(q)        # first sample for the rate counter
        self.available = True

    def _array(self, counter, fmt) -> list[tuple[str, float]]:
        size, count = wintypes.DWORD(0), wintypes.DWORD(0)
        rc = self._pdh.PdhGetFormattedCounterArrayW(counter, fmt, ctypes.byref(size), ctypes.byref(count), None)
        if rc != PDH_MORE_DATA or size.value == 0:
            return []
        buf = (ctypes.c_byte * size.value)()
        rc = self._pdh.PdhGetFormattedCounterArrayW(counter, fmt, ctypes.byref(size), ctypes.byref(count), buf)
        if rc != ERROR_SUCCESS:
            return []
        items = ctypes.cast(buf, ctypes.POINTER(_FmtItem))
        out = []
        for i in range(count.value):
            it = items[i]
            if it.FmtValue.CStatus in (0, 1):    # PDH_CSTATUS_VALID_DATA / NEW_DATA
                v = it.FmtValue.u.doubleValue if fmt & PDH_FMT_DOUBLE else float(it.FmtValue.u.largeValue)
                out.append((it.szName or "", v))
        return out

    def sample(self) -> tuple[float | None, int | None]:
        """(utilisation %, dedicated GPU memory in use, bytes); None for what is not available."""
        if not self.available:
            return None, None
        with self._lock:
            if self._pdh.PdhCollectQueryData(self._query) != ERROR_SUCCESS:
                return None, None
            util = None
            if self._engine is not None:
                per_type: dict[str, float] = defaultdict(float)
                for name, v in self._array(self._engine, PDH_FMT_DOUBLE | PDH_FMT_NOCAP100):
                    etype = name.rsplit("engtype_", 1)[-1] if "engtype_" in name else "other"
                    per_type[etype] += v
                if per_type:
                    util = min(100.0, max(per_type.values()))
            mem = None
            if self._memory is not None:
                vals = self._array(self._memory, PDH_FMT_LARGE)
                if vals:
                    mem = int(sum(v for _, v in vals))
            return util, mem

    def close(self) -> None:
        if self._query is not None:
            self._pdh.PdhCloseQuery(self._query)
            self._query = None
        self.available = False


_monitor: GpuMonitor | None = None
_mlock = threading.Lock()


def read_gpu_stats() -> tuple[float | None, int | None]:
    global _monitor
    with _mlock:
        if _monitor is None:
            _monitor = GpuMonitor()
            if not _monitor.available:
                log.info("GPU counters unavailable: %s", _monitor.error)
    return _monitor.sample()
