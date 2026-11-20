"""Application context: wires persistence, logging, monitoring. UI-agnostic (testable headless)."""
from __future__ import annotations

import logging
import os

from vcstudio import __version__
from vcstudio.core.capture_supervisor import CaptureSupervisor
from vcstudio.core.config import Settings
from vcstudio.core.database import Database
from vcstudio.core.logging_setup import RingBufferHandler, setup_logging
from vcstudio.core.paths import AppPaths
from vcstudio.core.performance import SystemMonitor
from vcstudio.core.profiles import ProfileError, ProfileManager

log = logging.getLogger("vcstudio.app")


class AppContext:
    def __init__(self, paths: AppPaths | None = None, *, configure_logging: bool = True):
        from vcstudio.core.pyobjc_prewarm import prewarm
        prewarm()                     # before any worker thread touches PyObjC (lazy symbols are not thread-safe)
        self.paths = (paths or AppPaths.default()).ensure()
        self.settings = Settings(self.paths)
        problems = self.settings.load()
        self.ring: RingBufferHandler | None = None
        if configure_logging:
            lc = self.settings.get("logging")
            self.ring = setup_logging(self.paths.logs_dir, lc["level"], lc["max_bytes"], lc["backup_count"])
        for p in problems:
            log.warning("settings: %s", p)
        self.db = Database(self.paths.database_file)
        self.profiles = ProfileManager(self.paths)
        self.profiles.load_all()
        wanted = self.settings.get("active_profile")
        try:
            self.profiles.switch(wanted)
        except ProfileError:
            log.warning("Active profile %r missing; using %r", wanted, self.profiles.names()[0])
            self.profiles.switch(self.profiles.names()[0])
        self.profiles.add_listener(self._on_profile_event)
        from vcstudio.core.performance import memory_thresholds_mb
        self.monitor = SystemMonitor(memory_thresholds_mb(self.settings)[0])
        self.capture = CaptureSupervisor(event_sink=self.db.log_event)
        from vcstudio.ai.engine import AIEngine
        from vcstudio.ai.model_manager import ModelManager
        self.models = ModelManager(self.paths.models_dir)
        self.ai = AIEngine(self.capture, self.models, event_sink=self.db.log_event)
        self.ai.load_mq(self.paths.root / "auto_model.json")
        self.ai.baseline = lambda: self.settings.get("performance.auto_baseline")
        from vcstudio.roi.resolver import RoiService
        self.rois = RoiService()
        from vcstudio.replacement.assets import AssetLibrary
        from vcstudio.replacement.service import ReplacementService
        self.assets = AssetLibrary(self.paths.assets_dir)
        self.replacement = ReplacementService(self.assets, self.settings.get("processing.compositor"))
        from vcstudio.core.video_pipeline import CameraOutputController, VideoPipeline
        from vcstudio.core.watchdog import Watchdog
        o = self.settings.get("output")
        self.camera_out = CameraOutputController(o["camera_device"], o["camera_sink"], o["camera_enabled"],
                                                 o["pixel_format"])
        self.pipeline = VideoPipeline(self.capture, self.ai, self.rois, self.replacement, self.camera_out,
                                      event_sink=self.db.log_event)
        from vcstudio.replacement.background import BackgroundService
        self.background = BackgroundService(self.capture, self.assets)       # blur / replace behind people
        self.pipeline.background = self.background
        self.background.video_frame = self._background_video_frame
        # Resource scheduler: one governor for the optional heavy modules (vcstudio/core/scheduler.py). It watches
        # output vs input rate (only while the pipeline runs) and slows background segmentation first, then the
        # detector. The AI engine's own load probe is off: the scheduler sets its rate factor.
        from vcstudio.core import maintenance
        from vcstudio.core.scheduler import Module, ResourceScheduler
        self.ai.load_probe = None
        self.scheduler = ResourceScheduler(
            lambda: ((self.pipeline.output_fps(), self.capture.stats().measured_fps)
                     if self.pipeline.running else (0.0, 0.0)),
            event_sink=lambda level, msg: self.db.log_event(level, "scheduler", msg),
            maintenance=maintenance.busy)
        seg = self.background.seg
        self.scheduler.add(Module(
            "background", priority=1, min_scale=1 / 3,
            active=lambda: self.background.active and seg.running,
            apply=lambda s, why: setattr(seg, "rate_scale", s),
            rate_fps=seg.rate_fps, cost_ms=lambda: seg.ms,
            state=lambda: "error" if seg.error else ("running" if seg.running else "stopped")))
        self.scheduler.add(Module(
            "detector", priority=2, min_scale=self.ai.adaptive.min_scale,
            active=lambda: self.ai.running and self.ai.config()[0].enabled,
            apply=self.ai.set_rate_scale,
            rate_fps=lambda: self.ai.stats().achieved_fps, cost_ms=lambda: self.ai.stats().inference_ms_avg,
            state=lambda: self.ai.stats().state + (" (standby)" if not self.ai.config()[0].detector_needed else "")))
        self.watchdog = Watchdog(self)
        from vcstudio.core.scene import SceneManager
        self.scenes = SceneManager(self)
        from vcstudio.core.recorder import RecordingService
        self.recording = RecordingService(self.pipeline, self.settings,
                                          lambda lv, m: self.db.log_event(lv, "recording", m))
        from vcstudio.plugins import PluginManager
        self.plugins = PluginManager(self.paths.root / "plugins", self.settings,
                                     lambda lv, m: self.db.log_event(lv, "plugins", m))
        self.pipeline.plugins = self.plugins
        # extra cameras and layouts (vcstudio/core/multicam.py): a CaptureSupervisor per extra source
        from vcstudio.core.multicam import MulticamService, SourceSet
        self.multicam = MulticamService(SourceSet(self.factory_for, self.db.log_event, self.source_loop_risk))
        self.pipeline.multicam = self.multicam
        if not os.environ.get("VCS_NO_PLUGINS"):            # safe start: VCS_NO_PLUGINS=1 loads none
            self.plugins.start_approved()
        from vcstudio.ai.gestures import GestureService
        self.gestures = GestureService(self.capture, self.settings,
                                       lambda lv, m: self.db.log_event(lv, "gestures", m),
                                       cache_dir=self.models.cache_dir / "onnx")
        self.gestures.apply_settings()                      # starts it when enabled
        from vcstudio.ai.vision import VisionService
        self.vision = VisionService(self.capture, self.settings, self.gestures,
                                    lambda lv, m: self.db.log_event(lv, "vision", m),
                                    cache_dir=self.models.cache_dir / "onnx")
        self.pipeline.vision = self.vision
        self.scheduler.add(Module(
            "vision", priority=0, min_scale=1 / 4,
            active=lambda: self.vision.running,
            apply=lambda s, why: setattr(self.vision, "rate_scale", s),
            rate_fps=self.vision.rate_fps, cost_ms=lambda: self.vision.stats.ms,
            state=lambda: self.vision.stats.state))
        self.vision.apply_settings()
        from vcstudio.replacement.privacy_shield import PrivacyShield
        self.shield = PrivacyShield(self.capture, self.settings,
                                    lambda lv, m: self.db.log_event(lv, "privacy", m))
        self.pipeline.shield = self.shield
        self.shield.face_outlines = self._face_outlines
        self.shield.apply_settings()
        # scene automation (vcstudio/core/automation.py): decisions here, actions through the existing services
        from vcstudio.core.automation import AutomationEngine
        self.action_sink = None                             # set by the main window: action id -> its handler
        self.ui_call = None                                 # set by the main window: run fn on the Qt thread
        self.automation = AutomationEngine(self._automation_snapshot, self.run_action_main,
                                           lambda lv, m: self.db.log_event(lv, "automation", m))
        self.gestures.listeners.append(lambda _aid, g: self.automation.emit("gesture", gesture=g))
        self.vision.listeners.append(lambda ev: self.automation.emit("gesture", gesture=ev))   # body: hand_raised
        self.automation.configure(self.settings.get("automation"))
        from vcstudio.app.remote import RemoteServer
        self.remote = RemoteServer(self, self.paths.root / "remote_token.bin")
        if not os.environ.get("VCS_NO_REMOTE"):
            self.remote.apply_settings()
        mode =self.profiles.active()["body"].get("performance_mode")
        if mode and mode != self.settings.get("performance.mode"):
            self.settings.set("performance.mode", mode)
        self.apply_ai_config()
        self.settings.add_listener(self._on_setting)
        crashed = self.db.unclean_previous_sessions()
        self.db.begin_session(__version__, self.profiles.active_name)
        if crashed:
            self.db.log_event("WARNING", "app", f"{crashed} previous session(s) did not exit cleanly")
            log.warning("%d previous session(s) did not exit cleanly", crashed)
        self.db.log_event("INFO", "app", "started", {"version": __version__})
        log.info("AI Virtual Camera Studio %s started; data dir %s", __version__, self.paths.root)
        try:
            from vcstudio.core.hardware import profile
            log.info("Hardware: %s", profile().summary())
        except Exception as e:                   # the profile only informs; it must never stop the start
            log.warning("Hardware profile unavailable: %s", e)

    def apply_ai_config(self) -> None:
        from vcstudio.ai.detection import DetectionConfig
        from vcstudio.ai.tracking import TrackerConfig
        body = self.profiles.active()["body"]
        self.rois.load_profile(body)
        self.replacement.load_profile(body)
        if hasattr(self, "background"):
            self.background.load_profile(body)
            # everything of the new scene is loaded (incl. the decoded background image): publish it in one go
            from vcstudio.core.scene import build_state
            self.pipeline.scene = build_state(self.profiles.active_name, self.rois, self.replacement, self.background,
                                              body)
            if self.pipeline.running:
                self.background.sync()
            if hasattr(self, "multicam"):
                self.multicam.apply(self.pipeline.scene.multicam, self.pipeline.running)
        self.ai.configure(DetectionConfig.from_profile(body), self.settings.get("performance.mode"),
                          TrackerConfig.from_profile(body))

    def _on_setting(self, key: str, _value) -> None:
        if key.startswith("performance.memory"):
            from vcstudio.core.performance import memory_thresholds_mb
            warn, crit = memory_thresholds_mb(self.settings)
            self.monitor.set_warning_mb(warn)
            self.watchdog.set_thresholds(warn, crit)
            log.info("Memory thresholds: warning %d MB, critical %d MB", warn, crit)
            return
        if key.startswith("recording"):
            self.recording.apply_settings()
            return
        if key.startswith("gestures"):
            self.gestures.apply_settings()
            if hasattr(self, "vision"):
                self.vision.apply_settings()                # hands: reuse gestures' result or run its own
            return
        if key.startswith("vision"):
            self.vision.apply_settings()
            return
        if key.startswith("privacy_shield"):
            self.shield.apply_settings()
            return
        if key.startswith("automation"):
            self.automation.configure(self.settings.get("automation"))
            return
        if key == "remote" or key.startswith("remote."):
            self.remote.apply_settings()
            return
        if key == "audio" or key.startswith("audio."):
            self.recording.apply_audio_fx()
            return
        if key.startswith("performance"):
            self.apply_ai_config()
        if key.startswith("output"):
            o = self.settings.get("output")
            self.camera_out.configure(o["camera_device"], o["camera_sink"], o["camera_enabled"], o["pixel_format"])
        if key.startswith("processing"):
            used = self.replacement.compositor.set_backend(self.settings.get("processing.compositor"))
            log.info("Compositor backend: %s", used)

    def _face_outlines(self, W: int, H: int) -> list:
        """Privacy shield "faces": face-mesh ovals (or face boxes) of the newest fresh AI Vision result, in output
        pixels; empty when AI Vision is off or its result is stale."""
        import numpy as np

        from vcstudio.ai.vision import STALE_S
        from vcstudio.core.frame import host_time_s
        r, _ = self.vision.slot.peek()
        if r is None or not self.vision.cfg.enabled or host_time_s() - r.pts > STALE_S + 0.5:
            return []
        sx, sy = W / max(1, r.width), H / max(1, r.height)
        out = []
        for f in r.faces:
            if f.mesh is not None:
                p = f.mesh.oval().astype(np.float32)
            else:
                x0, y0, x1, y1 = f.box
                p = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], np.float32)
            out.append((p * [sx, sy]).astype(np.int32))
        return out

    def _automation_snapshot(self):
        from vcstudio.core.automation import Snapshot
        from vcstudio.core.frame import host_time_s
        objs = self.ai.tracked_at(host_time_s()) if self.ai.running else []
        sources = {"main": self.capture.stats().state in ("running", "frozen") or not self.capture.running}
        for sid, st in self.multicam.sources.stats().items():
            sources[sid] = st.state in ("running", "frozen", "starting")
        return Snapshot(sum(o.label == "person" for o in objs), sum(o.label == "face" for o in objs),
                        frozenset(o.label for o in objs), self.recording.recording, self.profiles.active_name or "",
                        sources)

    def call_main(self, fn, timeout: float = 10.0):
        """Run fn on the UI thread when there is a window (profile listeners update widgets), else directly.
        Used by automation and the remote API, which decide on their own threads."""
        import threading
        if self.ui_call is None or threading.current_thread() is threading.main_thread():
            return fn()                                     # already on the UI thread: never wait for ourselves
        box: dict = {}
        done = threading.Event()

        def run():
            try:
                box["r"] = fn()
            except Exception as e:                          # noqa: BLE001 - handed back to the caller
                box["e"] = e
            finally:
                done.set()
        self.ui_call(run)
        if not done.wait(timeout):
            return "timed out"
        if "e" in box:
            raise box["e"]
        return box.get("r")

    def run_action_main(self, act: dict) -> str:
        return self.call_main(lambda: self.run_action(act))

    def run_action(self, act: dict) -> str:
        """Run one automation / remote-control action through the existing services; returns a short result."""
        t = act.get("type")
        if t == "scene":
            name = str(act.get("name") or "")
            if name not in self.profiles.names():
                return f"no scene named {name!r}"
            self.scenes.switch(name)                        # atomic: the existing SceneState publishing
            return f"scene {name}"
        if t == "privacy":
            from vcstudio.replacement import privacy_mode as pm
            lv = act.get("level") if act.get("level") in pm.LEVELS else "faces"
            self.update_active_profile(lambda b: pm.set_level(b, lv))
            if lv != "off" and not self.pipeline.replacement_enabled:
                self.pipeline.set_replacement_enabled(True)
            return f"privacy {lv}"
        if t == "record":
            if act.get("do") == "stop":
                st = self.recording.stop_recording() if self.recording.recording else None
                return "recording stopped" if st is not None else "was not recording"
            if self.recording.recording:
                return "already recording"
            return f"recording to {self.recording.start_recording().name}"
        if t == "replay":
            if self.recording.replay is None:
                return "instant replay is off"
            import threading
            threading.Thread(target=self.recording.save_replay, name="replay-save", daemon=True).start()
            return "saving replay"
        if t == "camera":
            sid = str(act.get("id") or "main")
            self.multicam.override = None if sid == "main" else sid
            return f"camera {sid}"
        if t == "layout":
            from vcstudio.core.multicam import LAYOUTS
            lay = act.get("layout")
            if lay not in LAYOUTS:
                return f"unknown layout {lay!r}"
            self.multicam.layout_override = lay
            return f"layout {lay}"
        if t == "color_preset":
            p = (self.settings.get("color_presets") or {}).get(act.get("name"))
            if not p:
                return f"no colour preset {act.get('name')!r}"
            self.update_active_profile(lambda b: b.__setitem__("color", {**p, "enabled": True}))
            return f"colour preset {act.get('name')}"
        if t == "effects":
            self.pipeline.set_replacement_enabled(bool(act.get("on", True)))
            return "effects on" if act.get("on", True) else "effects off"
        if t == "action":
            aid = str(act.get("id") or "")
            from vcstudio.ui.actions import ACTION_SPECS
            if aid not in {a.id for a in ACTION_SPECS} or aid.startswith("automation_"):
                return f"unknown action {aid!r}"
            if self.action_sink is None:
                return "no window to run this action"
            self.action_sink(aid)
            return f"action {aid}"
        return f"unknown action type {t!r}"

    def _background_video_frame(self, asset_id: str, t: float):
        """A looping video background: the compositor's player pool decodes it on its own thread; this only asks
        for the frame of output time t (None until the first frame is decoded)."""
        from vcstudio.replacement.assets import assets_from_profile
        from vcstudio.replacement.video import PlaybackSettings
        asset = assets_from_profile(self.profiles.active()["body"]).get(asset_id)
        if asset is None or asset.kind != "video":
            return None
        comp = self.replacement.compositor
        player = comp.videos.get(("__background__", asset_id), self.assets.path(asset), self.assets.video_info(asset),
                                 PlaybackSettings(loop=True))
        return None if player.failed else player.frame_at(t)

    def asset_in_use_elsewhere(self, asset_id: str, except_profile: str | None = None) -> bool:
        for n in self.profiles.names():
            if n == except_profile:
                continue
            if any(a.get("id") == asset_id for a in self.profiles.get(n)["body"].get("assets", [])):
                return True
        return False

    def update_active_profile(self, mutate) -> None:
        """Apply `mutate(body)` to the active profile, persist, and reconfigure engines."""
        name = self.profiles.active_name
        body = self.profiles.get(name)["body"]
        mutate(body)
        self.profiles.update_body(name, body)

    def _on_profile_event(self, event: str, name: str) -> None:
        if event == "switched" and hasattr(self, "replacement"):
            self.replacement.compositor.reset_assignments()   # a new scene hands out its assets afresh
        if event in ("switched", "updated", "renamed") and hasattr(self, "ai"):
            self.apply_ai_config()
        if event in ("switched", "renamed") and self.profiles.active_name:
            self.settings.set("active_profile", self.profiles.active_name)
        if event == "switched":
            mode = self.profiles.active()["body"].get("performance_mode")
            if mode and mode != self.settings.get("performance.mode"):
                self.settings.set("performance.mode", mode)
        self.db.log_event("INFO", "profiles", f"{event}: {name}")

    # ---------------- capture ----------------
    def capture_factory(self):
        """Build a source factory from current input settings."""
        return self.factory_for(self.settings.get("input"))

    def factory_for(self, spec: dict):
        """Source factory for an input description (the Input page's settings, or an extra multi-camera source:
        method + device / screen / file keys; missing size keys default to the main input's preferred format)."""
        from vcstudio.core.video_capture import TestPatternSource, device_source
        main = self.settings.get("input")
        i = {**{k: main[k] for k in ("preferred_width", "preferred_height", "preferred_fps")},
             "device_unique_id": None, "device_name_hint": "", **spec}
        w, h, fps = i["preferred_width"], i["preferred_height"], i["preferred_fps"]
        if i["method"] == "test_pattern":
            return lambda: TestPatternSource(w, h, fps)
        if i["method"] == "obs_ipc":
            from vcstudio.core.obs_shm_source import ObsShmSource
            return lambda: ObsShmSource()
        if i["method"] == "video_file":
            from vcstudio.core.video_file_source import VideoFileSource
            path, loop = i.get("video_path") or "", bool(i.get("video_loop", True))
            start = float(i.get("video_start_s") or 0.0)
            return lambda: VideoFileSource(path, loop, start_s=start)
        if i["method"] == "screen":
            from vcstudio.platform.win.screen_capture import ScreenCaptureSource
            target = i.get("screen_target") or "monitor:0"
            return lambda: ScreenCaptureSource(target, fps)
        method = i["method"]
        return lambda: device_source(method, i["device_unique_id"], i["device_name_hint"], w, h, fps)

    def start_capture(self) -> None:
        risk = self.feedback_loop_risk()
        if risk:
            log.error(risk)
            self.db.log_event("ERROR", "pipeline", risk)
            raise RuntimeError(risk)
        i = self.settings.get("input")
        sup = self.capture
        sup.missing_timeout = i["missing_input_timeout_ms"] / 1000
        sup.frozen_timeout = i["frozen_frame_timeout_ms"] / 1000
        sup.auto_reconnect = i["auto_reconnect"]
        log.info("Starting capture (%s)", i["method"])
        sup.start(self.capture_factory())
        self.ai.start()
        self.background.start()
        self.pipeline.start()
        self.watchdog.start()
        self.scheduler.start()
        sc = self.pipeline.scene
        self.multicam.apply(sc.multicam if sc is not None else None, True)

    def feedback_loop_risk(self) -> str:
        """Non-empty if the configured input is the device we output to (would feed back into itself)."""
        i, o = self.settings.get("input"), self.settings.get("output")
        from vcstudio.platform import is_device_method
        if is_device_method(i["method"]) and o["camera_enabled"] and \
                (i.get("device_name_hint") or "").casefold() == o["camera_device"].casefold():
            return f"Input '{i['device_name_hint']}' is also the output camera: this would feed back into itself."
        return ""

    def source_loop_risk(self, spec: dict) -> str:
        """Non-empty if an extra source is the output camera itself."""
        o = self.settings.get("output")
        from vcstudio.core.devices import is_own_output
        from vcstudio.platform import is_device_method
        name = spec.get("device_name_hint") or ""
        if is_device_method(spec.get("method", "")) and is_own_output(name, ""):
            return f"'{name}' is this app's own camera: it would show itself."
        if is_device_method(spec.get("method", "")) and o["camera_enabled"] and \
                name.casefold() == o["camera_device"].casefold():
            return f"'{name}' is the output camera: it would feed back into itself."
        return ""

    def stop_capture(self) -> None:
        self.multicam.stop()
        self.scheduler.stop()
        self.watchdog.stop()
        self.pipeline.stop()
        self.background.stop()
        self.ai.stop()
        self.capture.stop()
        self.replacement.compositor.release_resources()

    def shutdown(self) -> None:
        log.info("Shutting down")
        self.gestures.stop()
        self.vision.stop()
        self.shield.stop()
        self.automation.stop()
        self.remote.stop()
        self.multicam.stop()
        self.scheduler.stop()
        self.watchdog.stop()
        self.pipeline.stop()
        self.recording.close()             # after the pipeline: the file gets every frame that was output
        self.plugins.close()
        self.background.stop()
        self.replacement.close()
        self.ai.stop()
        self.capture.stop()
        self.db.log_event("INFO", "app", "stopped")
        self.db.end_session()
        self.settings.save()
        self.db.close()
