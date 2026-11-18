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
        if not os.environ.get("VCS_NO_PLUGINS"):            # safe start: VCS_NO_PLUGINS=1 loads none
            self.plugins.start_approved()
        from vcstudio.ai.gestures import GestureService
        self.gestures = GestureService(self.capture, self.settings,
                                       lambda lv, m: self.db.log_event(lv, "gestures", m),
                                       cache_dir=self.models.cache_dir / "onnx")
        self.gestures.apply_settings()                      # starts it when enabled
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
            return
        if key.startswith("performance"):
            self.apply_ai_config()
        if key.startswith("output"):
            o = self.settings.get("output")
            self.camera_out.configure(o["camera_device"], o["camera_sink"], o["camera_enabled"], o["pixel_format"])
        if key.startswith("processing"):
            used = self.replacement.compositor.set_backend(self.settings.get("processing.compositor"))
            log.info("Compositor backend: %s", used)

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
        from vcstudio.core.video_capture import TestPatternSource, device_source
        i = self.settings.get("input")
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

    def feedback_loop_risk(self) -> str:
        """Non-empty if the configured input is the device we output to (would feed back into itself)."""
        i, o = self.settings.get("input"), self.settings.get("output")
        from vcstudio.platform import is_device_method
        if is_device_method(i["method"]) and o["camera_enabled"] and \
                (i.get("device_name_hint") or "").casefold() == o["camera_device"].casefold():
            return f"Input '{i['device_name_hint']}' is also the output camera: this would feed back into itself."
        return ""

    def stop_capture(self) -> None:
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
