# 0.6.0 Professional — roadmap

Fast track: every phase extends the existing component named in [PRO_060_ARCHITECTURE.md](PRO_060_ARCHITECTURE.md);
no second scheduler, scene manager, pipeline, capture manager, model manager or event system.

| Phase | Scope | Builds on | New files |
|---|---|---|---|
| 0 | audit, architecture, feature matrix, roadmap | — | docs/PRO_060_* |
| 1 | real backend smoke inference; model import for any category with interface check, SHA-256, sample run, saved report | core/hardware.py, ai/model_import.py | — |
| 2 | secondary sources (camera / screen / window / file), layouts PiP / side by side / grid / custom, age-limited frames, auto switch with cooldown + hysteresis | CaptureSupervisor, LatestSlot, SceneState | core/multicam.py |
| 3 | face landmarks + head pose (YuNet points, solvePnP), body pose (MediaPipe Pose), overlays, scheduler module | YuNetFaceDetector, ResourceScheduler, hands.py | ai/pose.py, ai/head_pose.py |
| 4 | background colour / gradient layers, mask quality modes, manual track lock | replacement/background.py, ai/tracking.py | — |
| 5 | text regions (PP-OCRv3 DB), sensitive-pattern matching for recognised text, strict fail-safe | replacement/privacy_mode.py | ai/text_detect.py, replacement/privacy_text.py |
| 6 | grading controls, .cube LUT (1D/3D), per-scene, applied once per frame | lighting stage of VideoPipeline, SceneState | replacement/color.py |
| 7 | triggers, conditions, actions, cooldown, priority, history, loop protection | action bus, SceneManager | core/automation.py |
| 8 | gate, EQ, compressor, AGC, limiter, level meter; virtual audio device feasibility report | core/audio.py, recorder | core/audio_fx.py, reports/VIRTUAL_AUDIO_DEVICE.md |
| 9 | localhost HTTP + WebSocket API, DPAPI token, rate limit, mobile page | action bus | app/remote.py |
| 10 | integration, regression, performance, security review, reports | — | reports/PRO_060_* |

Out of scope for 0.6.0 (recorded, not promised): a signed virtual audio driver, SAM 2.1 video propagation (the
ONNX export has no memory-attention modules), depth estimation (no model approved), GPU LUT in the D3D11 shader
(the CPU path is measured first), identity recognition (never).
