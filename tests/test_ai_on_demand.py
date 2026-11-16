"""AI on demand (0.5 audit D20, roadmap P1.1): the detector runs at full rate only while some region follows
detected objects; otherwise at a 2 fps standby rate (previews keep their boxes, the model stays loaded)."""
from __future__ import annotations

from vcstudio.ai.detection import DetectionConfig
from vcstudio.core.profiles import default_profile_body
from vcstudio.roi.model import new_rect, set_quick_face


def cfg_for(body) -> DetectionConfig:
    return DetectionConfig.from_profile(body)


def test_which_profiles_need_the_detector():
    b = default_profile_body()
    b["rois"] = []
    assert not cfg_for(b).detector_needed                      # nothing uses detections
    b["rois"] = [new_rect("logo", 0.1, 0.1, 0.3, 0.3).to_dict()]
    assert not cfg_for(b).detector_needed                      # manual regions only
    set_quick_face(b, True)
    assert cfg_for(b).detector_needed                          # "Hide face" follows faces
    b["rois"][-1]["enabled"] = False
    assert not cfg_for(b).detector_needed                      # a disabled auto region does not count
    b["rois"] = [dict(new_rect("people", 0, 0, 1, 1).to_dict(), mode="hybrid")]
    assert cfg_for(b).detector_needed
    b["rois"] = []
    b.setdefault("ai", {})["open_vocab"] = {"enabled": True, "queries": ["cup"]}
    assert cfg_for(b).detector_needed                          # any object by text


def test_engine_uses_the_standby_rate_only_when_nothing_needs_it(tmp_path):
    from vcstudio.ai.engine import STANDBY_FPS, AIEngine
    from vcstudio.ai.model_manager import ModelManager
    from vcstudio.core.capture_supervisor import CaptureSupervisor
    eng = AIEngine(CaptureSupervisor(), ModelManager(tmp_path))
    eng.configure(DetectionConfig(detector_needed=False), "performance")
    assert eng.target_fps() == STANDBY_FPS
    eng.configure(DetectionConfig(detector_needed=False, frequency_mode="fixed", fixed_fps=1.0), "performance")
    assert eng.target_fps() == 1.0                              # a slower fixed user rate is kept
    eng.configure(DetectionConfig(detector_needed=True), "performance")
    assert eng.target_fps() > STANDBY_FPS                       # the mode's own rate
    eng.configure(DetectionConfig(detector_needed=False), "max_quality")
    assert eng.target_fps() == STANDBY_FPS                      # "as fast as possible" becomes standby too
