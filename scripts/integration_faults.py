"""Stage 9 integration check on Windows: live OBS -> vcstudio-obs plugin -> AI -> replacement -> "AI Virtual Camera"
-> a client app, with faults injected while a client keeps the camera open.

Needs OBS running the "VCS-E2E" scene (scripts/obs_e2e_scene.py) with the vcstudio-obs plugin, and the camera
installed (vcam-setup.exe --install). OBS is closed and restarted by this script (gracefully, like the user would).

    python scripts/integration_faults.py out.json

Phases (a client captures the camera the whole time and classifies every frame):
  A  baseline                 app frames with the replacement visible
  B  OBS closed, restarted    the camera keeps delivering frames; the app reconnects to OBS by itself
  C  client closes, reopens   the camera serves the next client at once
  D  app quits, starts again  the camera shows its waiting card, then the app's frames again
Each phase reports what the client saw, the longest gap between client frames, and recovery times.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from obs_e2e_scene import BG_BGR, NAME as OBS_SCENE  # noqa: E402

OBS_DIR = Path(r"C:\Program Files\obs-studio\bin\64bit")
CARD_BGR = (40, 32, 28)                    # native/win/vcam/vcam_frames.cpp waiting card background


def classify(img: np.ndarray) -> str:
    h, w = img.shape[:2]
    marker = img[int(h * .1):int(h * .2), int(w * .1):int(w * .2), :3].reshape(-1, 3).astype(int)
    if ((marker[:, 0] > 200) & (marker[:, 1] < 60) & (marker[:, 2] > 200)).mean() > 0.5:
        return "replaced"
    px = img[h // 2, int(w * .05), :3].astype(int)
    if np.abs(px - BG_BGR).max() <= 12:
        return "app_no_replacement"
    if np.abs(px - CARD_BGR).max() <= 6:
        return "card"
    return "other"


# ------------------------------------------------------------------------------------------------ OBS
def obs_pids() -> list[int]:
    import psutil
    return [p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == "obs64.exe"]


def visible_windows(pids: list[int]) -> list[tuple[int, str]]:
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    out: list[tuple[int, str]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _):
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if pid.value in pids and u.IsWindowVisible(h):
            buf = ctypes.create_unicode_buffer(256)
            u.GetWindowTextW(h, buf, 256)
            out.append((h, buf.value))
        return True
    u.EnumWindows(cb, 0)
    return out


def obs_close(timeout: float = 20) -> float:
    """Close OBS like a user would: WM_CLOSE to its windows, dialogs first, no /F. A modal dialog such as
    "New update available" (OBS shows it at start when an update exists) blocks the main window until it is
    closed, and `taskkill` without /F does not close it."""
    import ctypes
    WM_CLOSE = 0x0010
    t0, last = time.perf_counter(), -10.0
    while obs_pids() and time.perf_counter() - t0 < timeout:
        if time.perf_counter() - last > 1.5:
            wins = visible_windows(obs_pids())
            dialogs = [h for h, t in wins if not t.startswith("OBS ")]
            for h in dialogs or [h for h, _ in wins]:
                ctypes.windll.user32.PostMessageW(h, WM_CLOSE, 0, 0)
            last = time.perf_counter()
        time.sleep(0.1)
    if obs_pids():
        raise RuntimeError("OBS did not close")
    return time.perf_counter() - t0


def desktop_env(**extra: str) -> dict:
    """Environment for a desktop Qt app started from here, without the QT_* variables a test run sets
    (QT_QPA_PLATFORM=offscreen etc.): OBS has no offscreen plugin and shows "no Qt platform plugin could be
    initialized"; the studio app would start without a visible window."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("QT_")}
    env.update(extra)
    return env


def obs_start() -> None:
    sentinel = Path(os.environ["APPDATA"]) / "obs-studio" / ".sentinel"
    for f in sentinel.glob("run_*"):
        f.unlink(missing_ok=True)                 # a clean exit leaves none; never show the safe-mode dialog
    subprocess.Popen([str(OBS_DIR / "obs64.exe"), "--collection", OBS_SCENE, "--profile", OBS_SCENE,
                      "--disable-shutdown-check"], cwd=str(OBS_DIR), env=desktop_env())
    # not --minimize-to-tray: a window hidden in the tray ignores WM_CLOSE, so obs_close could not end it


# ------------------------------------------------------------------------------------------------ client
class Client:
    """A camera client (DirectShow, like OBS / Discord / most apps) that records what it receives."""

    def __init__(self):
        self.log: list[tuple[float, str]] = []
        self.src = None

    def start(self) -> None:
        from vcstudio.platform.win.capture import WindowsCameraSource
        self.src = WindowsCameraSource("directshow", None, "AI Virtual Camera", 1920, 1080, 30, allow_own_output=True)
        self.src.start(lambda f: self.log.append((time.perf_counter(), classify(f.array))), lambda k, m: None)

    def stop(self) -> None:
        if self.src:
            self.src.stop()
            self.src = None

    def window(self, t0: float, t1: float) -> list[tuple[float, str]]:
        return [e for e in self.log if t0 <= e[0] < t1]


def summary(frames: list[tuple[float, str]], t0: float, t1: float) -> dict:
    kinds: dict[str, int] = {}
    for _, k in frames:
        kinds[k] = kinds.get(k, 0) + 1
    ts = [t0] + [t for t, _ in frames] + [t1]
    return {"seconds": round(t1 - t0, 1), "frames": len(frames), "fps": round(len(frames) / max(t1 - t0, 1e-3), 1),
            "kinds": kinds, "max_gap_s": round(max(b - a for a, b in zip(ts, ts[1:])), 2)}


def first(frames, kind: str, after: float) -> float | None:
    return next((round(t - after, 2) for t, k in frames if t >= after and k == kind), None)


# ------------------------------------------------------------------------------------------------ app
def make_app():
    import cv2
    from vcstudio.app.application import AppContext
    from vcstudio.replacement.spec import ReplacementSpec
    from vcstudio.roi.model import ROI, new_rect
    ctx = AppContext(configure_logging=False)
    home = Path(os.environ["VCSTUDIO_HOME"])
    clear, mag = home / "clear.png", home / "magenta.png"
    cv2.imwrite(str(clear), np.zeros((2, 2, 4), np.uint8))
    cv2.imwrite(str(mag), np.full((8, 8, 4), (255, 0, 255, 255), np.uint8))
    a, m = ctx.assets.import_file(clear), ctx.assets.import_file(mag)
    people = ROI(name="People", mode="auto", classes=["person"], padding=0.0,
                 replacement=ReplacementSpec(enabled=True, mode="object", asset_id=a.id,
                                             background=[255, 0, 255, 255]).to_dict())
    marker = new_rect("Marker", 0.05, 0.05, 0.25, 0.25)
    marker.replacement = ReplacementSpec(enabled=True, asset_id=m.id, fit="stretch").to_dict()
    ctx.update_active_profile(lambda b: (b.__setitem__("assets", [a.to_dict(), m.to_dict()]),
                                        b.__setitem__("rois", [people.to_dict(), marker.to_dict()])))
    ctx.settings.set("input.method", "obs_ipc")
    ctx.settings.set("output.camera_device", "AI Virtual Camera")
    ctx.start_capture()
    return ctx


def capture_state(ctx) -> str:
    try:
        return ctx.capture.stats().state
    except Exception as e:          # reported, not fatal
        return f"? {e}"


def main(out_path: str) -> int:
    os.environ.setdefault("VCSTUDIO_HOME", tempfile.mkdtemp(prefix="vcs-faults-"))
    res: dict = {"ok": False}
    client = Client()
    states: list[tuple[float, str]] = []
    ctx = None
    stop_watch = threading.Event()
    try:
        if not obs_pids():
            obs_start(); time.sleep(8)
        ctx = make_app()
        holder = {"ctx": ctx}

        def watch():
            while not stop_watch.is_set():
                c = holder["ctx"]
                states.append((time.perf_counter(), capture_state(c) if c else "app stopped"))
                time.sleep(0.1)
        threading.Thread(target=watch, daemon=True).start()
        time.sleep(4)                                                     # warm-up (AI model choice)
        client.start()
        time.sleep(2)

        # A: baseline
        a0 = time.perf_counter(); time.sleep(8); a1 = time.perf_counter()
        res["A_baseline"] = summary(client.window(a0, a1), a0, a1)

        # B: OBS closed for 3 s, then started again
        b0 = time.perf_counter()
        res["B_obs_close_s"] = round(obs_close(), 2)
        time.sleep(3)
        b_start = time.perf_counter(); obs_start()
        t_dead = time.perf_counter() + 60
        recovered = None
        while time.perf_counter() < t_dead:
            if any(t > b_start + 1 and s == "running" for t, s in states[-5:]) and \
                    any(t > b_start and k == "replaced" for t, k in client.log[-5:]):
                recovered = time.perf_counter(); break
            time.sleep(0.1)
        if recovered is None:                       # what OBS and the frame transfer look like when it fails
            from vcstudio.platform.win.obs_shm import obs_sharing_status
            res["B_diag"] = {"obs_pids": obs_pids(), "obs_windows": [t for _, t in visible_windows(obs_pids())],
                             "sharing": obs_sharing_status(), "last_states": [s for _, s in states[-5:]]}
        time.sleep(4); b1 = time.perf_counter()
        res["B_obs_restart"] = summary(client.window(b0, b1), b0, b1)
        res["B_capture_states"] = sorted({s for t, s in states if b0 <= t < b1})
        res["B_app_back_s_after_obs_start"] = round(recovered - b_start, 2) if recovered else None

        # C: client closes and reopens
        client.stop(); time.sleep(2)
        c0 = time.perf_counter(); client.start(); time.sleep(5); c1 = time.perf_counter()
        res["C_client_reopen"] = summary(client.window(c0, c1), c0, c1)
        res["C_first_frame_s"] = round(client.window(c0, c1)[0][0] - c0, 2) if client.window(c0, c1) else None

        # D: app quits, then starts again
        d0 = time.perf_counter()
        holder["ctx"] = None
        ctx.shutdown(); ctx = None
        time.sleep(6)
        d_start = time.perf_counter()
        ctx = make_app(); holder["ctx"] = ctx
        time.sleep(15); d1 = time.perf_counter()
        frames = client.window(d0, d1)
        res["D_app_restart"] = summary(frames, d0, d1)
        res["D_card_after_quit_s"] = first(frames, "card", d0)
        res["D_replaced_after_start_s"] = first(frames, "replaced", d_start)

        A, B, C, D = res["A_baseline"], res["B_obs_restart"], res["C_client_reopen"], res["D_app_restart"]
        res["ok"] = bool(
            A["kinds"].get("replaced", 0) >= 0.9 * A["frames"] and A["frames"] > 0
            and B["max_gap_s"] < 1.0 and res["B_app_back_s_after_obs_start"] is not None
            and C["frames"] > 0 and (res["C_first_frame_s"] or 99) < 3
            and D["max_gap_s"] < 1.0 and res["D_card_after_quit_s"] is not None
            and res["D_replaced_after_start_s"] is not None)
        return 0 if res["ok"] else 1
    except Exception as e:
        import traceback
        res["error"] = f"{type(e).__name__}: {e}"; res["traceback"] = traceback.format_exc()
        return 1
    finally:
        stop_watch.set()
        client.stop()
        if ctx is not None:
            ctx.shutdown()
        Path(out_path).write_text(json.dumps(res, indent=2), encoding="utf-8")
        print(json.dumps({k: v for k, v in res.items() if k != "traceback"}, indent=1))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "integration_faults.json"))
