"""Stage 9, live: OBS -> vcstudio-obs plugin -> AI -> replacement -> "AI Virtual Camera" -> a client app.

Opt-in (they start and close OBS and the app): VCS_LIVE_OBS=1 with OBS 30+, the vcstudio-obs plugin, the camera
installed (vcam-setup.exe --install) and the scene from scripts/obs_e2e_scene.py.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform != "win32" or os.environ.get("VCS_LIVE_OBS") != "1",
                                reason="set VCS_LIVE_OBS=1 with OBS + vcstudio-obs + AI Virtual Camera")


def _run(script: str, tmp_path: Path, *extra: str) -> dict:
    out = tmp_path / "result.json"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "obs_e2e_scene.py")], check=True, capture_output=True)
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / script), str(out), *extra],
                       capture_output=True, text=True, timeout=600)
    res = json.loads(out.read_text(encoding="utf-8"))
    assert r.returncode == 0 and res["ok"], json.dumps({k: v for k, v in res.items() if k != "traceback"}, indent=1)
    return res


def test_faults_obs_restart_client_reopen_app_restart(tmp_path):
    res = _run("integration_faults.py", tmp_path)
    assert res["B_obs_restart"]["max_gap_s"] < 1.0 and res["D_app_restart"]["max_gap_s"] < 1.0


def test_real_gui_app_feeds_the_camera(tmp_path):
    res = _run("gui_e2e.py", tmp_path)
    assert res["exit_code"] == 0
