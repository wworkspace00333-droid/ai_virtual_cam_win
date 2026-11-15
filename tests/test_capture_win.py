"""Windows capture: device enumeration, camera source, OBS direct frame transfer (shared memory).

The shared-memory tests use a Python writer that follows the plugin exactly (SEC_RESERVE section,
commit of header + used slot bytes, per-slot seqlock), so they run without OBS. Live tests against a
running OBS with the vcstudio-obs plugin: set VCS_LIVE_OBS=1.
"""
import ctypes
import os
import sys
import threading
import time
import uuid
from ctypes import wintypes

import numpy as np
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows capture")


# ------------------------------------------------------------------------------ writer emulation
class FakeObsWriter:
    """Writes frames like native/obs-plugin/vcstudio-obs.c does on Windows."""

    def __init__(self, name: str, w: int, h: int, fps=(30, 1)):
        from vcstudio.platform.win import obs_shm as m
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateFileMappingW.restype = wintypes.HANDLE
        k32.CreateFileMappingW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                           wintypes.DWORD, wintypes.LPCWSTR]
        k32.MapViewOfFile.restype = ctypes.c_void_p
        k32.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
        k32.VirtualAlloc.restype = ctypes.c_void_p
        k32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
        self.k32, self.m = k32, m
        self.slot_bytes = m.MAX_W * m.MAX_H * 4
        total = m.HEADER_BYTES + m.SLOTS * self.slot_bytes
        PAGE_READWRITE, SEC_RESERVE, MEM_COMMIT = 0x04, 0x04000000, 0x1000
        self.h = k32.CreateFileMappingW(wintypes.HANDLE(-1), None, PAGE_READWRITE | SEC_RESERVE, total >> 32,
                                        total & 0xFFFFFFFF, name)
        assert self.h, ctypes.get_last_error()
        self.base = k32.MapViewOfFile(self.h, 0xF001F, 0, 0, 0)
        assert self.base
        assert k32.VirtualAlloc(self.base, m.HEADER_BYTES, MEM_COMMIT, PAGE_READWRITE)
        for i in range(m.SLOTS):
            assert k32.VirtualAlloc(self.base + m.HEADER_BYTES + i * self.slot_bytes, w * h * 4, MEM_COMMIT, PAGE_READWRITE)
        self.w, self.h_px = w, h
        self._set(m.O_MAGIC, m.MAGIC, 4); self._set(m.O_VERSION, m.VERSION, 4); self._set(m.O_SLOTS, m.SLOTS, 4)
        self._set(m.O_HEADER, m.HEADER_BYTES, 4); self._set(m.O_SLOT_BYTES, self.slot_bytes, 8)
        self._set(m.O_PID, os.getpid(), 4); self._set(m.O_FPS_NUM, fps[0], 4); self._set(m.O_FPS_DEN, fps[1], 4)
        self._set(m.O_ACTIVE, 1, 4)
        self.n = 0

    def _set(self, off, v, size):
        (ctypes.c_uint32 if size == 4 else ctypes.c_uint64).from_address(self.base + off).value = v

    def _get(self, off):
        return ctypes.c_uint64.from_address(self.base + off).value

    def publish(self, img: np.ndarray, leave_open: bool = False) -> int:
        m = self.m
        n = self.n + 1
        i = n % m.SLOTS
        so = m.O_SLOT0 + i * m.SLOT_SIZE
        self._set(so, 2 * n - 1, 8)                      # odd: being written
        dst = np.ctypeslib.as_array((ctypes.c_uint8 * img.nbytes).from_address(self.base + m.HEADER_BYTES + i * self.slot_bytes))
        dst[:] = img.reshape(-1)
        self._set(so + 8, self.w, 4); self._set(so + 12, self.h_px, 4); self._set(so + 16, self.w * 4, 4)
        self._set(so + 20, 1, 4); self._set(so + 24, time.perf_counter_ns(), 8)
        if leave_open:                                   # simulate the writer being mid-frame
            self._set(m.O_LATEST, n, 8)
            self.n = n
            return n
        self._set(so, 2 * n, 8)
        self._set(m.O_LATEST, n, 8)
        self._set(m.O_HEARTBEAT, time.perf_counter_ns(), 8)
        self.n = n
        return n

    def stop(self):
        self._set(self.m.O_ACTIVE, 0, 4)

    def close(self):
        self.k32.UnmapViewOfFile(ctypes.c_void_p(self.base))
        self.k32.CloseHandle(self.h)


@pytest.fixture
def shm_name():
    return f"Local\\vcs-test-{uuid.uuid4().hex[:8]}"


def _img(w, h, value):
    a = np.zeros((h, w, 4), np.uint8)
    a[..., 0], a[..., 1], a[..., 2], a[..., 3] = value, 255 - value, 7, 255
    return a


def test_status_without_writer(shm_name):
    from vcstudio.platform.win.obs_shm import obs_sharing_status
    st = obs_sharing_status(shm_name)
    assert not st["available"] and "not sharing" in st["reason"]


def test_reader_gets_newest_consistent_frame(shm_name):
    from vcstudio.platform.win.capture import FramePool
    from vcstudio.platform.win.obs_shm import ShmView, obs_sharing_status
    w = FakeObsWriter(shm_name, 320, 180)
    try:
        st = obs_sharing_status(shm_name)
        assert not st["available"] and st["reason"] == "no recent frames"   # mapped but nothing published yet
        for v in (10, 20, 30):
            w.publish(_img(320, 180, v))
        st = obs_sharing_status(shm_name)
        assert st["available"] and (st["width"], st["height"], st["fps"]) == (320, 180, 30.0) and st["frames"] == 3
        view, pool = ShmView(shm_name), FramePool()
        n, img, ts = view.read_latest(pool, 0)
        assert n == 3 and img.shape == (180, 320, 4) and img[5, 5, 0] == 30     # newest wins, older skipped
        assert view.read_latest(pool, 3) is None                                  # nothing newer
        w.publish(_img(320, 180, 40), leave_open=True)                            # torn: odd sequence number
        assert view.read_latest(pool, 3) is None
        w.stop()
        assert obs_sharing_status(shm_name)["reason"] == "OBS stopped sharing"
        view.close()
    finally:
        w.close()


def test_obs_source_streams_and_reports_disconnect(shm_name):
    from vcstudio.platform.win.obs_shm import ObsShmSource
    w = FakeObsWriter(shm_name, 640, 360)
    stop = threading.Event()

    def writer():
        v = 0
        while not stop.is_set():
            w.publish(_img(640, 360, v % 256)); v += 1
            time.sleep(1 / 60)
    w.publish(_img(640, 360, 0))
    t = threading.Thread(target=writer, daemon=True); t.start()
    frames, events = [], []
    src = ObsShmSource(shm_name)
    try:
        src.start(lambda f: frames.append((f.seq, f.width, f.height, int(f.array[0, 0, 0]), f.arrival - f.pts)),
                  lambda k, m: events.append(k))
        time.sleep(1.0)
        assert src.nominal[:2] == (640, 360)
        assert len(frames) >= 40, len(frames)                   # ~60 fps writer
        assert all(fw == 640 and fh == 360 for _, fw, fh, _, _ in frames)
        assert all(0 <= lat < 0.1 for *_, lat in frames)        # OBS timestamps share our clock (QPC)
        stop.set(); t.join()
        w.stop()
        time.sleep(0.5)
        assert "disconnected" in events
    finally:
        stop.set()
        src.stop()
        w.close()


def test_frame_pool_never_overwrites_a_frame_in_use():
    from vcstudio.platform.win.capture import FramePool
    pool = FramePool()
    held = pool.get(4, 4)                 # e.g. the AI thread still reads this frame
    held[:] = 1
    other = pool.get(4, 4)
    assert other is not held
    del other                             # released by everyone: reusable
    again = pool.get(4, 4)
    assert again is not held
    view = held[1:3]                      # a view keeps its base alive
    del held
    nxt = pool.get(4, 4)
    assert nxt.base is None and view.base is not nxt
    assert len(pool._bufs) <= FramePool.MAX


def test_device_enumeration_is_consistent():
    from vcstudio.platform.win import devices as d
    ds = d.backend_devices("directshow")
    assert [x.index for x in ds] == list(range(len(ds)))         # OpenCV opens by this index
    union = d.list_video_devices()
    assert {x.unique_id or x.name for x in ds} <= {u.unique_id for u in union}
    for u in union:
        assert u.model_id                                        # which API(s) list it


def test_missing_device_reports_clearly():
    from vcstudio.core.video_capture import CaptureError
    from vcstudio.platform.win.capture import WindowsCameraSource
    src = WindowsCameraSource("directshow", "dshow-clsid:{00000000-0000-0000-0000-000000000001}", "No Such Camera")
    with pytest.raises(CaptureError, match="not found"):
        src.start(lambda f: None, lambda k, m: None)
    assert not src.is_present()


def test_own_output_is_refused(monkeypatch):
    from vcstudio.core.video_capture import FeedbackLoopRefused
    from vcstudio.platform.win import capture as c
    from vcstudio.platform.win.devices import BackendDevice
    monkeypatch.setattr(c, "resolve_device", lambda *a: BackendDevice(0, "AI Virtual Camera", "dshow-clsid:{X}", "directshow"))
    with pytest.raises(FeedbackLoopRefused):
        c.WindowsCameraSource("directshow", None, "AI Virtual Camera").start(lambda f: None, lambda k, m: None)


def test_settings_with_utf8_bom_are_not_reset(paths):
    """Notepad / Windows PowerShell 5 save UTF-8 with a BOM; such a settings.json must load, not reset."""
    from vcstudio.core.config import Settings
    paths.settings_file.write_bytes(b"\xef\xbb\xbf" + '{"input": {"method": "obs_ipc"}}'.encode())
    s = Settings(paths)
    assert s.load() == [] and s.get("input.method") == "obs_ipc"
    assert not paths.settings_file.with_suffix(".corrupt.json").exists()


def test_obs_plugin_paths():
    from vcstudio.core import obs_plugin
    assert obs_plugin._win_dll(obs_plugin._win_install_dir()).parts[-4:] == \
        ("vcstudio-obs", "bin", "64bit", "vcstudio-obs.dll")
    if obs_plugin.obs_install_dir():
        assert obs_plugin.obs_version().count(".") == 2


@pytest.mark.skipif(os.environ.get("VCS_LIVE_OBS") != "1", reason="set VCS_LIVE_OBS=1 with OBS + vcstudio-obs running")
def test_live_obs_direct_transfer():
    from vcstudio.core.obs_shm_source import ObsShmSource, obs_sharing_status
    st = obs_sharing_status()
    assert st["available"], st["reason"]
    frames = []
    src = ObsShmSource()
    src.start(lambda f: frames.append(f.arrival), lambda k, m: None)
    time.sleep(3)
    src.stop()
    fps = (len(frames) - 1) / (frames[-1] - frames[0])
    assert fps >= 0.9 * st["fps"], (fps, st)


def _mf_camera_free() -> bool:
    if sys.platform != "win32":
        return False
    from vcstudio.platform.win import mfcap
    from vcstudio.platform.win.devices import mf_devices
    if not mfcap.available():
        return False
    dev = next((d for d in mf_devices() if d.name.endswith("(Windows Virtual Camera)")), None)
    if dev is None:
        return False
    try:
        mfcap.MfCapture(dev.unique_id, 1280, 720, 30).close()
        return True
    except mfcap.MfCaptureError:        # in use by another app (Windows: one controlling app per camera)
        return False


@pytest.mark.skipif(not _mf_camera_free(), reason="needs vcs_mfcap.dll and the Windows 11 camera, not in use")
@pytest.mark.parametrize("w,h,fps", [(1920, 1080, 60), (1280, 720, 30)])
def test_media_foundation_capture_through_the_apps_own_reader(w, h, fps):
    """The "Media Foundation" capture method (vcs_mfcap.dll, the opencv-python wheels have no MSMF): the Windows 11
    "AI Virtual Camera" as the device, the app's frames as content."""
    import threading
    import time
    import numpy as np
    from vcstudio.platform.win.capture import WindowsCameraSource
    from vcstudio.platform.win.vcam_writer import VcamWriter
    wr = VcamWriter(); wr.configure(1920, 1080, 60)
    img = np.zeros((1080, 1920, 4), np.uint8); img[..., 1] = 40; img[..., 2] = 200; img[..., 3] = 255
    stop = threading.Event()

    def send():
        k, t0 = 0, time.perf_counter()
        while not stop.is_set():
            k += 1; wr.publish(img); time.sleep(max(0.0, t0 + k / 60 - time.perf_counter()))
    threading.Thread(target=send, daemon=True).start()
    got = []
    src = WindowsCameraSource("mediafoundation", None, "AI Virtual Camera", w, h, fps, allow_own_output=True)
    try:
        time.sleep(0.3)
        src.start(lambda f: got.append((time.perf_counter(), f.width, f.height, f.array[h // 2, w // 2, :3].tolist())),
                  lambda k, m: None)
        time.sleep(0.5); n0, t0 = len(got), time.perf_counter(); time.sleep(3)
        rate = (len(got) - n0) / (time.perf_counter() - t0)
    finally:
        src.stop(); stop.set(); wr.close()
    assert src.nominal[:2] == (w, h) and abs(src.nominal[2] - fps) < 0.5
    assert rate >= 0.85 * fps, rate
    b, g, r = got[-1][3]
    assert b < 12 and abs(g - 40) < 12 and abs(r - 200) < 12, got[-1]       # BT.601 declared and honoured
