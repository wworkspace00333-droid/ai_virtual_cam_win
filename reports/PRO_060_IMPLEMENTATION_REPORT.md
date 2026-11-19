# 0.6.0 Professional — implementation report

Baseline 0.5.3 (published 2026-11-19). Work done 2026-11-19 on the development server (Windows Server 2025, 2-core
Xeon, 31 GB RAM, no hardware GPU). Nothing is committed yet: all changes are in the working tree of `windows-port`
(27 files changed, +728 / −51 lines; about 7 000 new lines in new files, a third of them tests).

Principle followed: every phase extends an existing component (capture supervisor, LatestSlot, SceneState, resource
scheduler, action bus, model download table, background service, tracker, recorder). No second pipeline, scheduler,
scene manager, capture manager, model manager or event system was created.

## Phase 0 — audit
Files: docs/PRO_060_ARCHITECTURE.md (component map), docs/PRO_060_FEATURE_MATRIX.md, docs/PRO_060_ROADMAP.md.

## Phase 1 — hardware & model manager
- New: `vcstudio/ai/model_check.py` (9 categories, interface validation, per-provider sample run, SHA-256, registry,
  tamper check), `vcstudio/ui/studio/models_tab.py`.
- Changed: `core/hardware.py` (+`verify_backends`: real inference per provider), `app/diagnostics.py` (backend checks in
  the ZIP).
- Result: 7 bundled / downloaded models pass their category; wrong categories are refused with a reason. DirectML on
  this server: listed, falls back to CPU — now reported as such.
- Not done: driver versions and DirectX feature levels in the profile.

## Phase 2 — multi-camera studio
- New: `core/multicam.py` (SourceSet of CaptureSupervisors, layouts, compose, AutoSwitcher, MulticamService),
  `ui/studio/multicam_tab.py`.
- Changed: `core/scene.py` (SceneState.multicam), `core/video_pipeline.py` (composition stage), `app/application.py`
  (`factory_for`, `source_loop_risk`, service wiring), actions `camera_next` / `layout_next` + hotkeys.
- Not done: active-speaker switching (no reliable signal), two physical webcams not available here.

## Phase 3 — AI vision
- New: `ai/head_pose.py`, `ai/pose.py`, `ai/vision.py`, `ui/studio/vision_tab.py`; downloads POSE (17 MB).
- Changed: `ai/detection.py` (Detection.points), `ai/onnx_engine.py` (YuNet keeps 5 landmarks), `ai/gestures.py`
  (hands timestamp, listeners for unmapped gestures), scheduler module "vision" (priority 0).
- Not done: dense face mesh, body-gesture events, body-aware framing (pose boxes are available for it).

## Phase 4 — background studio & tracking
- Changed: `replacement/background.py` (colour, gradient, video; quality modes), `ai/person_segmentation.py`
  (guided filter), `ai/tracking.py` (lock, re-acquire, history), replacement page background card.
- Not done: depth effects (no model), SAM 2.1 video propagation (not in the ONNX export).

## Phase 5 — privacy shield
- New: `ai/text_detect.py`, `replacement/privacy_shield.py`, `ui/studio/privacy_tab.py`; download TEXT (2.4 MB).
- Changed: pipeline shield stage (also in passthrough when strict; dark frame if the shield fails in strict mode).
- Not done: text recognition model (16 MB CRNN) not downloaded — needs the user's approval; until then "sensitive"
  falls back to hiding all text.

## Phase 6 — colour studio
- New: `replacement/color.py`, `ui/studio/color_tab.py`; settings `color_presets`.
- Changed: pipeline grading stage (after lighting, once per frame), SceneState.color.
- Not done: D3D11 GPU path (CPU path measured and documented).

## Phase 7 — automation
- New: `core/automation.py`, `ui/studio/automation_tab.py`; actions `automation_1..4`.
- Changed: `AppContext.run_action` / `call_main` (actions on the UI thread), gesture listeners.

## Phase 8 — audio studio
- New: `core/audio_fx.py`, `ui/studio/audio_tab.py`, reports/VIRTUAL_AUDIO_DEVICE.md.
- Changed: `core/recorder.py` (chain before the recorder, live meters on demand).
- Not done: monitoring playback, multiple inputs, virtual microphone (driver).

## Phase 9 — remote control
- New: `app/remote.py` (HTTP + WebSocket + page), `ui/studio/remote_tab.py`.

## Phase 10 — integration
- New Studio page (`ui/pages/studio_page.py`, 8 tabs) between Scenes and Profiles; Russian strings in
  `vcstudio/i18n_ru_060.py` (merged, older translations take precedence).
- New settings sections are deep-merged over existing settings files (no migration step needed); new profile
  sections (`multicam`, `color`, extended `background`) default to off when absent, so old profiles load unchanged.
- Docs: README, CHANGELOG (Unreleased), THIRD_PARTY_NOTICES (3 new models), scripts/fetch_models.py `--pose --text`,
  scripts/pro060_bench.py.

## New dependencies
None. Everything uses packages already in the venv (numpy, OpenCV, ONNX Runtime, SciPy, PySide6, PyAV) and the
standard library (http.server, ctypes DPAPI). New downloadable models: MediaPipe person + pose, PP-OCRv3 text
detection (all Apache-2.0, pinned, SHA-256 checked).
