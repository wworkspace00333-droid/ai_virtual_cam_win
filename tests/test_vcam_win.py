"""Windows "AI Virtual Camera": shared-memory protocol, output controller, and live loopback through the
installed DirectShow camera (skipped when the camera is not registered: run build\\native\\vcam-setup.exe --install)."""
import re
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows virtual camera")
ROOT = Path(__file__).resolve().parents[1]


def _installed_name() -> str | None:
    """Name DirectShow clients see: the DirectShow camera, or on Windows 11 the Media Foundation camera, which
    Frame Server also exposes to DirectShow apps as "AI Virtual Camera (Windows Virtual Camera)"."""
    if sys.platform != "win32":
        return None
    from vcstudio.platform.win.devices import backend_devices
    from vcstudio.platform.win.vcam_status import components
    if not any(c.state == "installed" for c in components()):
        return None
    names = [d.name for d in backend_devices("directshow")]
    return next((n for n in ("AI Virtual Camera", "AI Virtual Camera (Windows Virtual Camera)") if n in names), None)


CAMERA = _installed_name()
needs_camera = pytest.mark.skipif(CAMERA is None, reason="AI Virtual Camera not installed")


def test_python_writer_matches_the_c_header():
    """vcam_writer.py hard-codes offsets of vcam_shm.h; the header's static_asserts pin the same values."""
    from vcstudio.platform.win import vcam_writer as vw
    h = (ROOT / "native" / "win" / "vcam" / "vcam_shm.h").read_text(encoding="utf-8")
    assert re.search(r"VCAM_SHM_MAGIC 0x%08Xu" % vw.MAGIC, h)
    assert f"VCAM_MAX_WIDTH {vw.MAX_W}u" in h and f"VCAM_MAX_HEIGHT {vw.MAX_H}u" in h
    assert f"offsetof(vcam_shm_header, slot) == {vw.O_SLOT0}" in h
    assert vw.SLOTS == 3 and vw.HEADER == 4096 and vw.SLOT_SIZE == 32


def test_writer_publishes_with_seqlock():
    import ctypes
    from vcstudio.platform.win.vcam_writer import O_HEARTBEAT, O_LATEST, O_SLOT0, SLOT_SIZE, VcamWriter
    w = VcamWriter("Local\\vcam-test-" + str(time.time_ns()))
    try:
        w.configure(640, 360, 30)
        img = np.full((360, 640, 4), 7, np.uint8)
        w.publish(img); w.publish(img)
        n = ctypes.c_uint64.from_address(w.base + O_LATEST).value
        seq = ctypes.c_uint64.from_address(w.base + O_SLOT0 + (n % 3) * SLOT_SIZE).value
        assert n == 2 and seq == 2 * n
        assert abs(ctypes.c_uint64.from_address(w.base + O_HEARTBEAT).value - time.perf_counter_ns()) < 1e9
        with pytest.raises(ValueError):
            w.publish(np.zeros((1200, 2000, 4), np.uint8))
    finally:
        w.close()


def test_output_refuses_other_devices():
    from vcstudio.camera.output import CameraOutputError, VirtualCameraOutput
    with pytest.raises(CameraOutputError) as e:
        VirtualCameraOutput("OBS Virtual Camera").open()
    assert e.value.code == -1


def _bars(k: int) -> np.ndarray:
    img = np.zeros((1080, 1920, 4), np.uint8)
    img[..., 3] = 255
    img[..., 2] = 200
    for b in range(10):
        img[0:120, b * 192:(b + 1) * 192, :3] = 255 if (k >> b) & 1 else 0
    return img


def _decode(a: np.ndarray) -> int:
    h = a.shape[0]
    row = a[h * 60 // 1080, :, :3].astype(int).mean(axis=1)
    cell = a.shape[1] / 10
    return sum(1 << b for b in range(10) if row[int((b + 0.5) * cell)] > 128)


@needs_camera
@pytest.mark.parametrize("w,h,fps", [(1920, 1080, 30), (1280, 720, 30), (1920, 1080, 60)])
def test_loopback_through_the_directshow_camera(w, h, fps):
    """App -> shared memory -> AI Virtual Camera (DirectShow) -> a client app (OpenCV) at several formats."""
    from vcstudio.camera.output import VirtualCameraOutput
    from vcstudio.platform.win.capture import WindowsCameraSource
    out = VirtualCameraOutput()
    out.open()
    stop = threading.Event()

    def send():
        k, t0 = 0, time.perf_counter()
        while not stop.is_set():
            k += 1
            out.send(_bars(k), time.perf_counter())
            time.sleep(max(0.0, t0 + k / 30 - time.perf_counter()))
    t = threading.Thread(target=send, daemon=True)
    t.start()
    got = []
    src = WindowsCameraSource("directshow", None, CAMERA, w, h, fps, allow_own_output=True)
    try:
        src.start(lambda f: got.append((f.width, f.height, _decode(f.array), f.array[h // 2, w // 2, :3].tolist())),
                  lambda k, m: None)
        time.sleep(3)
    finally:
        src.stop()
        stop.set()
        t.join()
        out.close()
    assert src.nominal[:2] == (w, h)
    # The Windows 11 Frame Server bridge (how DirectShow apps see the Media Foundation camera) delivers the first
    # native mode of the requested size, 30 fps, whatever rate is set (measured on build 26100 with YUY2, NV12 and
    # no FOURCC). Media Foundation clients get 60: test_media_foundation_camera_through_frame_server.
    expect = min(fps, 30) if CAMERA.endswith("(Windows Virtual Camera)") else fps
    assert len(got) >= 0.8 * 3 * expect, len(got)
    nums = [n for _, _, n, _ in got[10:]]
    assert len(set(nums)) >= 30 and all(b >= a for a, b in zip(nums, nums[1:])), nums[:20]   # fresh, in order
    b, g, r = got[-1][3]
    assert b < 15 and g < 15 and abs(r - 200) < 15                     # colour survives the format conversion


@needs_camera
def test_camera_shows_waiting_card_without_the_app():
    from vcstudio.platform.win.capture import WindowsCameraSource
    got = []
    src = WindowsCameraSource("directshow", None, CAMERA, 1280, 720, 30, allow_own_output=True)
    try:
        src.start(lambda f: got.append(f.array[10, 10, :3].tolist()), lambda k, m: None)
        time.sleep(2)
    finally:
        src.stop()
    assert len(got) >= 45                                   # the device keeps streaming without the app
    assert all(abs(a - b) <= 3 for a, b in zip(got[-1], [40, 32, 28]))   # the card's background colour


def test_windows_11_camera_name_counts_as_own_output():
    """Windows lists the Media Foundation camera as "AI Virtual Camera (Windows Virtual Camera)"; capturing it
    as input would feed the output back into itself."""
    from vcstudio.core.devices import is_own_output
    assert is_own_output("AI Virtual Camera (Windows Virtual Camera)", "")
    assert is_own_output("AI Virtual Camera", "")
    assert not is_own_output("AI Virtual Camera Pro", "") and not is_own_output("OBS Virtual Camera", "")


def test_same_device_under_two_interface_classes_is_listed_once(monkeypatch):
    from vcstudio.platform.win import devices as d
    base = r"\\?\swd#vcamdevapi#abc#{%s}\{fcebba03-9d13-4c13-9940-cc84fcd132d1}"
    monkeypatch.setattr(d, "backend_devices", lambda m: (
        [d.BackendDevice(0, "Cam", base % "65e8773d-8f56-11d0-a3b9-00a0c9223196", "directshow")] if m == "directshow"
        else [d.BackendDevice(0, "Cam", base % "e5323777-f976-4f5b-9b55-b94699c46e44", "mediafoundation")]))
    devs = d.list_video_devices()
    assert len(devs) == 1 and devs[0].model_id == "DirectShow + Media Foundation"


MF_DLL = ROOT / "build" / "native" / "vcam_mf.dll"
MF_CLIENT = ROOT / "build" / "native" / "mf_client_test.exe"


@pytest.mark.skipif(not (MF_DLL.exists() and MF_CLIENT.exists()), reason="run scripts/build_native_win.ps1")
def test_media_foundation_source_in_process():
    """The Media Foundation camera's media source read through IMFSourceReader (as Frame Server would host it):
    the waiting card without the app, the app's frames with it."""
    import subprocess
    from vcstudio.platform.win.vcam_writer import VcamWriter

    def read():
        r = subprocess.run([str(MF_CLIENT), "--inproc", str(MF_DLL), "45"], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stdout + r.stderr
        m = re.search(r"frames (\d+) in [\d.]+ s \(([\d.]+) fps\), centre BGRA (\w\w) (\w\w) (\w\w)", r.stdout)
        assert m, r.stdout
        return int(m[1]), float(m[2]), tuple(int(x, 16) for x in m.group(3, 4, 5))
    n, fps, px = read()
    assert n == 45 and 25 <= fps <= 35 and px == (40, 32, 28)          # 1080p30 NV12/RGB32 card
    w = VcamWriter()
    stop = threading.Event()
    img = np.zeros((1080, 1920, 4), np.uint8); img[..., 1] = 40; img[..., 2] = 200; img[..., 3] = 255

    def send():
        while not stop.is_set():
            w.publish(img)
            time.sleep(1 / 30)
    w.configure(1920, 1080, 30)
    t = threading.Thread(target=send, daemon=True); t.start()
    try:
        time.sleep(0.2)
        n, fps, px = read()
    finally:
        stop.set(); t.join(); w.close()
    assert n == 45 and px == (0, 40, 200)


MF_DEVICE = "AI Virtual Camera (Windows Virtual Camera)"


def _mf_installed() -> bool:
    if sys.platform != "win32" or not MF_CLIENT.exists():
        return False
    import subprocess
    r = subprocess.run([str(MF_CLIENT), "--list"], capture_output=True, text=True, timeout=30)
    return MF_DEVICE in r.stdout


@pytest.mark.skipif(not _mf_installed(), reason="Media Foundation camera not installed (vcam-setup.exe --install, "
                                                "Windows 11 with camera access allowed)")
@pytest.mark.parametrize("native,fps", [(1, 60), (3, 60), (4, 30)])   # NV12 1080p60, NV12 720p60, RGB32 1080p30
def test_media_foundation_camera_through_frame_server(native, fps):
    """The registered Windows 11 camera opened by name like Zoom / the Camera app: Frame Server (session 0) hosts
    the media source, which reads the app's frames from this session. NV12 is decoded here as BT.601 limited
    range, which the media type declares."""
    import subprocess
    from vcstudio.platform.win.vcam_writer import VcamWriter

    def read(n):
        r = subprocess.run([str(MF_CLIENT), "--device", MF_DEVICE, str(n), str(native), "raw"],
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stdout + r.stderr
        assert re.search(r"native %d: .*matrix (2|0), range (2|0)  <- selected" % native, r.stdout), r.stdout
        m = re.search(r"frames (\d+) in [\d.]+ s \(([\d.]+) fps\), centre BGRA (\w\w) (\w\w) (\w\w)", r.stdout)
        assert m, r.stdout
        return int(m[1]), float(m[2]), [int(x, 16) for x in m.group(3, 4, 5)]
    n, rate, px = read(fps)
    assert n == fps and abs(rate - fps) < 0.1 * fps
    assert all(abs(a - b) <= 4 for a, b in zip(px, (40, 32, 28))), px            # waiting card without the app
    w = VcamWriter()
    stop = threading.Event()
    img = np.zeros((1080, 1920, 4), np.uint8); img[..., 1] = 40; img[..., 2] = 200; img[..., 3] = 255

    def send():
        k, t0 = 0, time.perf_counter()
        while not stop.is_set():
            k += 1
            w.publish(img)
            time.sleep(max(0.0, t0 + k / 60 - time.perf_counter()))
    w.configure(1920, 1080, 60)
    t = threading.Thread(target=send, daemon=True); t.start()
    try:
        time.sleep(0.3)
        n, rate, px = read(2 * fps)
    finally:
        stop.set(); t.join(); w.close()
    assert n == 2 * fps and abs(rate - fps) < 0.1 * fps
    assert all(abs(a - b) <= 4 for a, b in zip(px, (0, 40, 200))), px            # the app's frames


@needs_camera
def test_status_reports_installed_and_visible():
    from vcstudio.camera import camera_manager as cm
    s = cm.status()
    assert s.extension_state == "installed" and s.device_visible and s.ready
