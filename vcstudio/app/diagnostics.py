"""'Save diagnostics': a local ZIP for support, without personal data. Nothing is sent anywhere.

Contents
  system.json     Windows build, CPU, memory, GPUs, ONNX Runtime providers, app version, frozen or source
  camera.json     camera registration state, camera name, visible devices (names only)
  selfcheck.json  the bundle self-check (models, compositor, camera components, decoding)
  settings.json   the app settings
  profiles/*.json the profiles' JSON (regions and choices; no images or videos)
  logs/*.log      the app log files (rotating, at most a few MB)
  events.json     the last 500 events of the app database (errors, restarts)
Personal data is replaced in every text file before it is written: the Windows user name, the computer name, the
home folder and the data folder become <user>, <computer>, <home>, <data>; e-mail-like strings become <email>.
Asset files (the user's images and videos) are never included, only their names inside the profiles.
"""
from __future__ import annotations

import datetime as _dt
import getpass
import json
import os
import platform
import re
import socket
import zipfile
from pathlib import Path

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def default_name() -> str:
    return f"vcstudio-diagnostics-{_dt.datetime.now():%Y%m%d-%H%M%S}.zip"


class Redactor:
    def __init__(self, data_root: Path):
        pairs = []
        home = str(Path.home())
        for raw, tag in ((str(data_root), "<data>"), (home, "<home>"), (home.replace("\\", "/"), "<home>"),
                         (home.replace("\\", "\\\\"), "<home>")):
            if raw and len(raw) > 3:
                pairs.append((raw, tag))
        for raw, tag in ((self._safe(getpass.getuser), "<user>"), (self._safe(socket.gethostname), "<computer>"),
                         (os.environ.get("COMPUTERNAME", ""), "<computer>"), (os.environ.get("USERNAME", ""), "<user>")):
            if raw and len(raw) > 1:
                pairs.append((raw, tag))
        # longest first, so "C:\Users\name\AppData" is replaced before "name"
        self.pairs = sorted(set(pairs), key=lambda p: -len(p[0]))

    @staticmethod
    def _safe(fn) -> str:
        try:
            return fn() or ""
        except Exception:
            return ""

    def __call__(self, text: str) -> str:
        for raw, tag in self.pairs:
            # whole-word for short names (a user called "an" must not eat every "an")
            if len(raw) < 6 and raw.isalnum():
                text = re.sub(rf"(?<![A-Za-z0-9]){re.escape(raw)}(?![A-Za-z0-9])", tag, text, flags=re.IGNORECASE)
            else:
                text = re.sub(re.escape(raw), tag.replace("\\", "\\\\"), text, flags=re.IGNORECASE)
        return EMAIL.sub("<email>", text)


def _system() -> dict:
    from vcstudio import __version__
    from vcstudio.core.resources import frozen
    d: dict = {"app_version": __version__, "frozen": frozen(), "python": platform.python_version(),
               "platform": platform.platform()}
    try:
        from vcstudio.platform.win.sysinfo import inspect_environment
        d.update(inspect_environment())
    except Exception as e:
        d["environment_error"] = str(e)
    return d


def _camera() -> dict:
    d: dict = {}
    try:
        from vcstudio.camera import camera_manager
        st = camera_manager.status()
        d.update(state=st.extension_state, installed=st.extension_enabled, visible=st.device_visible,
                 message=st.message)
        from vcstudio.platform.win.vcam_status import camera_name, components
        d["name"] = camera_name()
        d["components"] = [{"name": c.name, "state": c.state, "file_exists": c.file_exists} for c in components()]
        from vcstudio.core.devices import list_video_devices
        d["devices"] = [v.name for v in list_video_devices()]
    except Exception as e:
        d["error"] = str(e)
    return d


def write_zip(ctx, out: Path, include_selfcheck: bool = True) -> dict:
    """Write the diagnostics ZIP; returns {'files': n, 'kb': size}."""
    red = Redactor(ctx.paths.root)
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        def put(name: str, text: str) -> None:
            nonlocal n
            z.writestr(name, red(text))
            n += 1

        put("system.json", json.dumps(_system(), indent=1, default=str))
        put("camera.json", json.dumps(_camera(), indent=1, default=str))
        if include_selfcheck:
            try:
                import tempfile

                from vcstudio.app.selftest import bundle_selfcheck
                tmp = Path(tempfile.mkdtemp()) / "selfcheck.json"
                bundle_selfcheck(str(tmp))
                put("selfcheck.json", tmp.read_text(encoding="utf-8"))
            except Exception as e:
                put("selfcheck.json", json.dumps({"error": str(e)}))
        sf = ctx.paths.settings_file
        if sf.exists():
            put("settings.json", sf.read_text(encoding="utf-8-sig"))
        for p in sorted(ctx.paths.profiles_dir.glob("*.json")):
            put(f"profiles/{red(p.name)}", p.read_text(encoding="utf-8-sig"))
        logs = ctx.paths.logs_dir
        if logs.exists():
            for p in sorted(logs.glob("*.log*"))[:6]:
                put(f"logs/{p.name}", p.read_text(encoding="utf-8", errors="replace")[-2_000_000:])
        try:
            events = ctx.db.recent_events(500)
        except Exception as e:
            events = [{"error": str(e)}]
        put("events.json", json.dumps(events, indent=1, default=str))
        put("README.txt", "AI Virtual Camera Studio diagnostics. Personal data is replaced with <user>, <computer>, "
                          "<home>, <data>, <email>. Your images and videos are not included.\n")
    return {"files": n, "kb": round(out.stat().st_size / 1024)}
