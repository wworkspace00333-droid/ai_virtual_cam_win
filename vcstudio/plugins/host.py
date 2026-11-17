"""Plugin host: discovery without running code, approval bound to SHA-256, guarded calls (see vcstudio/plugins)."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import logging
import re
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np

log = logging.getLogger(__name__)

API_VERSION = 1
MAX_FAILURES = 3                 # in a row: the plugin is switched off
SLOW_MS = 15.0                   # average above this is shown as "slow" (the output has ~16 ms per frame at 60 fps)
ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


@dataclass(frozen=True)
class PluginObject:
    label: str
    track_id: int
    confidence: float
    box: tuple[float, float, float, float]          # x0, y0, x1, y1 in output pixels


@dataclass(frozen=True)
class FrameInfo:
    pts: float
    width: int
    height: int
    scene: str = ""
    objects: tuple[PluginObject, ...] = ()


@dataclass
class PluginInfo:
    id: str
    path: Path
    sha256: str
    name: str = ""
    description: str = ""
    api: int | None = None
    approved: bool = False          # switched on by the user for exactly this file content
    state: str = "off"              # off | running | error | incompatible | invalid
    error: str = ""
    calls: int = 0
    failures: int = 0
    avg_ms: float = 0.0

    @property
    def slow(self) -> bool:
        return self.state == "running" and self.calls > 30 and self.avg_ms > SLOW_MS


class PluginHostAPI:
    """What a plugin's create(host) receives."""

    def __init__(self, plugin_id: str, options: dict, logger: Callable[[str, str], None]):
        self.plugin_id = plugin_id
        self.options = options          # saved with the app settings (plugins.options.<id>) when the app saves
        self._log = logger
        self.api_version = API_VERSION

    def log(self, message: str, level: str = "INFO") -> None:
        self._log(level if level in ("INFO", "WARNING", "ERROR") else "INFO", f"{self.plugin_id}: {message}"[:500])


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_metadata(path: Path) -> dict[str, Any]:
    """NAME / DESCRIPTION / API_VERSION as literal top-level constants, read without executing the file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    meta: dict[str, Any] = {}
    has_create = False
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            key = node.targets[0].id
            if key in ("NAME", "DESCRIPTION", "API_VERSION"):
                try:
                    meta[key] = ast.literal_eval(node.value)
                except ValueError:
                    pass
        elif isinstance(node, ast.FunctionDef) and node.name == "create":
            has_create = True
    meta["has_create"] = has_create
    return meta


class _Loaded:
    __slots__ = ("info", "obj", "consecutive")

    def __init__(self, info: PluginInfo, obj):
        self.info, self.obj, self.consecutive = info, obj, 0


class PluginManager:
    """Owns the plugins folder and the running plugins. run() is called by the output thread; everything else by
    the UI thread. Settings: plugins.approved = {id: sha256}, plugins.options = {id: {...}}."""

    def __init__(self, folder: Path, settings, log_event: Callable[[str, str], None] | None = None):
        self.folder = Path(folder)
        self.settings = settings
        self._log = log_event or (lambda lv, m: None)
        self._lock = threading.Lock()
        self._active: tuple[_Loaded, ...] = ()
        self.infos: dict[str, PluginInfo] = {}
        self._options: dict[str, dict] = dict(settings.get("plugins.options") or {})

    def save_options(self) -> None:
        """Write the plugins' option dicts back to the settings (settings.get returns copies)."""
        try:
            self.settings.set("plugins.options", {k: v for k, v in self._options.items() if isinstance(v, dict)})
        except Exception as e:                       # e.g. a plugin put something that is not JSON in its options
            self._log("WARNING", f"plugin options not saved: {e}")

    # ---- settings
    def _approved(self) -> dict:
        return dict(self.settings.get("plugins.approved") or {})

    def _set_approved(self, d: dict) -> None:
        self.settings.set("plugins.approved", d)

    # ---- discovery (no plugin code runs)
    def discover(self) -> list[PluginInfo]:
        self.folder.mkdir(parents=True, exist_ok=True)
        approved = self._approved()
        running = {lp.info.id for lp in self._active}
        found: dict[str, PluginInfo] = {}
        for p in sorted(self.folder.glob("*.py")):
            pid = p.stem
            if not ID_RE.match(pid) or pid.startswith("_"):
                continue
            try:
                digest = sha256_of(p)
            except OSError as e:
                found[pid] = PluginInfo(pid, p, "", state="invalid", error=str(e))
                continue
            info = PluginInfo(pid, p, digest, approved=approved.get(pid) == digest)
            old = self.infos.get(pid)
            try:
                meta = read_metadata(p)
                info.name = str(meta.get("NAME") or pid)[:80]
                info.description = str(meta.get("DESCRIPTION") or "")[:300]
                api = meta.get("API_VERSION")
                info.api = api if isinstance(api, int) else None
                if info.api != API_VERSION:
                    info.state, info.error = "incompatible", f"API_VERSION {api!r}, this app supports {API_VERSION}"
                elif not meta["has_create"]:
                    info.state, info.error = "invalid", "no create(host) function"
            except (SyntaxError, UnicodeDecodeError, ValueError) as e:
                info.state, info.error = "invalid", f"{type(e).__name__}: {e}"[:300]
            if pid in running and old is not None and old.sha256 == digest:
                info = old                                       # keep the live record (stats, state)
            elif old is not None and old.state == "error" and old.sha256 == digest:
                info.state, info.error = old.state, old.error    # a failed plugin stays failed until re-enabled
            found[pid] = info
        self.infos = found
        for pid in running - {i for i, v in found.items() if v.approved}:
            self._unload(pid)                                    # deleted or changed on disk: stop it
        return list(found.values())

    # ---- enabling
    def enable(self, pid: str) -> PluginInfo:
        """Approve the plugin's current file content and start it. Returns its info (state running or error)."""
        info = self.infos.get(pid) or next((i for i in self.discover() if i.id == pid), None)
        if info is None:
            raise KeyError(pid)
        if info.state in ("incompatible", "invalid"):
            return info
        info.sha256 = sha256_of(info.path)                       # approve what is on disk now
        approved = self._approved()
        approved[pid] = info.sha256
        self._set_approved(approved)
        info.approved = True
        self._load(info)
        return info

    def disable(self, pid: str) -> None:
        approved = self._approved()
        approved.pop(pid, None)
        self._set_approved(approved)
        self._unload(pid)
        info = self.infos.get(pid)
        if info is not None:
            info.approved, info.state, info.error = False, "off", ""

    def start_approved(self) -> None:
        """At app start: load the plugins whose approved hash still matches the file."""
        for info in self.discover():
            if info.approved and info.state == "off":
                self._load(info)

    def _load(self, info: PluginInfo) -> None:
        self._unload(info.id)
        if sha256_of(info.path) != info.sha256:
            info.state, info.error = "off", "the file changed after it was approved"
            return
        modname = f"vcs_plugin_{info.id}"
        try:
            spec = importlib.util.spec_from_file_location(modname, info.path)
            if spec is None or spec.loader is None:
                raise ImportError("cannot load")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if getattr(mod, "API_VERSION", None) != API_VERSION:
                raise RuntimeError(f"API_VERSION {getattr(mod, 'API_VERSION', None)!r}")
            opts = self._options.setdefault(info.id, {})
            obj = mod.create(PluginHostAPI(info.id, opts, self._log))
            if not callable(getattr(obj, "process", None)):
                raise TypeError("create() must return an object with process(frame, info)")
        except BaseException as e:                                # incl. SystemExit from a careless plugin
            if isinstance(e, KeyboardInterrupt):
                raise
            info.state, info.error = "error", f"start: {type(e).__name__}: {e}"[:300]
            sys.modules.pop(modname, None)
            self._log("ERROR", f"plugin {info.id} did not start: {info.error}")
            return
        info.state, info.error, info.calls, info.failures, info.avg_ms = "running", "", 0, 0, 0.0
        with self._lock:
            self._active = self._active + (_Loaded(info, obj),)
        self._log("INFO", f"plugin {info.id} started ({info.name}, sha256 {info.sha256[:12]})")

    def _unload(self, pid: str) -> None:
        with self._lock:
            gone = [lp for lp in self._active if lp.info.id == pid]
            self._active = tuple(lp for lp in self._active if lp.info.id != pid)
        for lp in gone:
            close = getattr(lp.obj, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as e:
                    log.warning("plugin %s close failed: %s", pid, e)
            if lp.info.state == "running":
                lp.info.state = "off"
        sys.modules.pop(f"vcs_plugin_{pid}", None)

    def close(self) -> None:
        for lp in self._active:
            self._unload(lp.info.id)
        if self._options:
            self.save_options()

    # ---- the output thread
    @property
    def active(self) -> bool:
        return bool(self._active)

    def run(self, frame: np.ndarray, info: FrameInfo) -> np.ndarray:
        """Apply the running plugins in order. Never raises; a failing plugin is skipped (and stopped after
        MAX_FAILURES in a row). Returns `frame` itself when nothing changed."""
        cur = frame
        for lp in self._active:
            view = cur.view()
            view.flags.writeable = False
            t0 = time.perf_counter()
            try:
                res = lp.obj.process(view, info)
                if res is not None:
                    res = np.asarray(res)
                    if res.shape != cur.shape or res.dtype != np.uint8:
                        raise ValueError(f"returned {res.dtype} {res.shape}, expected uint8 {cur.shape}")
                    cur = np.ascontiguousarray(res)
                lp.consecutive = 0
            except BaseException as e:
                if isinstance(e, KeyboardInterrupt):
                    raise
                self._failed(lp, e)
            ms = (time.perf_counter() - t0) * 1000
            i = lp.info
            i.calls += 1
            i.avg_ms = ms if i.calls == 1 else 0.95 * i.avg_ms + 0.05 * ms
        return cur

    def _failed(self, lp: _Loaded, e: BaseException) -> None:
        lp.consecutive += 1
        lp.info.failures += 1
        lp.info.error = f"{type(e).__name__}: {e}"[:300]
        if lp.consecutive == 1:
            log.warning("plugin %s failed: %s", lp.info.id, lp.info.error)
        if lp.consecutive >= MAX_FAILURES:
            with self._lock:
                self._active = tuple(x for x in self._active if x is not lp)
            lp.info.state = "error"
            self._log("ERROR", f"plugin {lp.info.id} switched off after {MAX_FAILURES} failures in a row: "
                               f"{lp.info.error}")
            close = getattr(lp.obj, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
