"""Stage 9: the real GUI app, started the way a user starts it, feeding "AI Virtual Camera" from live OBS.

Prepares a fresh data folder (input: direct OBS transfer, "Start capture when the app opens" on, a People region
painted magenta and a fixed magenta marker), starts `python -m vcstudio.app.main` as its own process, captures
the camera like a client app, saves a screenshot of the desktop, then closes the app window (WM_CLOSE) and checks
that it exits and the camera falls back to its waiting card. Needs OBS with the VCS-E2E scene and the plugin.

    python scripts/gui_e2e.py out.json [screenshot.png]
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))

from integration_faults import (Client, desktop_env, make_app, obs_close, obs_pids, obs_start, summary,  # noqa: E402
                                visible_windows)


def main(out_path: str, shot: str | None) -> int:
    home = tempfile.mkdtemp(prefix="vcs-gui-e2e-")
    os.environ["VCSTUDIO_HOME"] = home
    res: dict = {"ok": False, "home": home}
    proc = None
    client = Client()
    started_obs = not obs_pids()             # OBS started here is closed again at the end
    try:
        if started_obs:
            obs_start(); time.sleep(8)
        ctx = make_app()                        # same profile as the fault test; saved to the data folder
        ctx.settings.set("input.autostart", True)
        ctx.stop_capture(); ctx.shutdown()
        env = desktop_env(VCSTUDIO_HOME=home, PYTHONPATH=str(ROOT))
        env.pop("VCS_NO_AUTOSTART", None)
        t0 = time.perf_counter()
        proc = subprocess.Popen([sys.executable, "-m", "vcstudio.app.main"], cwd=str(ROOT), env=env)
        import psutil
        pids = lambda: [proc.pid] + [c.pid for c in psutil.Process(proc.pid).children(recursive=True)]
        win = None
        while time.perf_counter() - t0 < 60 and win is None:
            win = next((w for w in visible_windows(pids()) if "AI Virtual Camera Studio" in w[1]), None)
            time.sleep(0.2)
        res["window_s"] = round(time.perf_counter() - t0, 1) if win else None
        res["window_title"] = win[1] if win else None
        time.sleep(8)                                               # autostart + AI warm-up
        client.start()
        a0 = time.perf_counter(); time.sleep(10); a1 = time.perf_counter()
        res["running"] = summary(client.window(a0, a1), a0, a1)
        if shot:
            import cv2
            from vcstudio.platform.win.screenshot import primary_screen
            cv2.imwrite(shot, primary_screen())
            res["screenshot"] = shot
        # close the window like the user (title-bar X)
        import ctypes
        c0 = time.perf_counter()
        if win:
            ctypes.windll.user32.PostMessageW(win[0], 0x0010, 0, 0)
        try:
            proc.wait(30); res["exit_code"] = proc.returncode
        except subprocess.TimeoutExpired:
            res["exit_code"] = None
        res["exit_s"] = round(time.perf_counter() - c0, 1)
        time.sleep(3); c1 = time.perf_counter()
        after = client.window(c0, c1)
        res["after_close"] = summary(after, c0, c1)
        r = res["running"]
        res["ok"] = bool(win and r["frames"] > 0 and r["kinds"].get("replaced", 0) >= 0.9 * r["frames"]
                         and res["exit_code"] == 0 and after and after[-1][1] == "card")
        return 0 if res["ok"] else 1
    except Exception as e:
        import traceback
        res["error"] = f"{type(e).__name__}: {e}"; res["traceback"] = traceback.format_exc()
        return 1
    finally:
        client.stop()
        if proc and proc.poll() is None:
            proc.kill()
        if started_obs and obs_pids():
            try:
                res["obs_closed_at_end_s"] = round(obs_close(), 2)
            except Exception as e:
                res["obs_close_error"] = str(e)
        Path(out_path).write_text(json.dumps(res, indent=2), encoding="utf-8")
        print(json.dumps({k: v for k, v in res.items() if k != "traceback"}, indent=1))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "gui_e2e.json", sys.argv[2] if len(sys.argv) > 2 else None))
