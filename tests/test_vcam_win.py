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


def _installed() -> bool:
    from vcstudio.platform.win.vcam_status import components
    return any(c.name.startswith("DirectShow") and c.state == "installed" for c in components())


needs_camera = pytest.mark.skipif(sys.platform != "win32" or not _installed(), reason="AI Virtual Camera not installed")


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
    src = WindowsCameraSource("directshow", None, "AI Virtual Camera", w, h, fps, allow_own_output=True)
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
    assert len(got) >= 0.8 * 3 * fps, len(got)
    nums = [n for _, _, n, _ in got[10:]]
    assert len(set(nums)) >= 30 and all(b >= a for a, b in zip(nums, nums[1:])), nums[:20]   # fresh, in order
    b, g, r = got[-1][3]
    assert b < 15 and g < 15 and abs(r - 200) < 15                     # colour survives the format conversion


@needs_camera
def test_camera_shows_waiting_card_without_the_app():
    from vcstudio.platform.win.capture import WindowsCameraSource
    got = []
    src = WindowsCameraSource("directshow", None, "AI Virtual Camera", 1280, 720, 30, allow_own_output=True)
    try:
        src.start(lambda f: got.append(f.array[10, 10, :3].tolist()), lambda k, m: None)
        time.sleep(2)
    finally:
        src.stop()
    assert len(got) >= 45                                   # the device keeps streaming without the app
    assert all(abs(a - b) <= 3 for a, b in zip(got[-1], [40, 32, 28]))   # the card's background colour


@needs_camera
def test_status_reports_installed_and_visible():
    from vcstudio.camera import camera_manager as cm
    s = cm.status()
    assert s.extension_state == "installed" and s.device_visible and s.ready
