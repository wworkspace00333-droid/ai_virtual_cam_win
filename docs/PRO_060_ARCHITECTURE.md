# 0.6.0 Professional — architecture (Phase 0 audit, 2026-11-19)

Baseline: version 0.5.3 (`vcstudio/__init__.py`), branch `windows-port`, 26 k lines of Python in `vcstudio/`,
78 test files. Audit of the code itself, not of older documents.

## What already exists and stays the base

| Component | Existing implementation | Completeness | Tests | Extension for 0.6.0 |
|---|---|---|---|---|
| Camera input (MF / DirectShow) | `platform/win/capture.py`, `mfcap.py`, `devices.py` | full | test_capture_win, live | used for secondary cameras |
| OBS integration | `core/obs_shm_source.py`, `platform/win/obs_shm.py`, `native/obs-plugin` | full (owner check in 0.5.3) | test_capture_win | none |
| Screen / window / video file input | `platform/win/screen_capture.py`, `core/video_file_source.py` | full | yes | used for secondary sources |
| Capture lifecycle | `core/capture_supervisor.py` (states, reconnect, frozen detection) | full | yes | one per secondary source |
| Latest-frame slots | `core/frame_queue.py` `LatestSlot` | full | yes | used by every new module |
| Virtual camera output | `camera/output.py`, `platform/win/vcam_writer.py`, `native/win` | full | live | none |
| D3D11 compositor | `platform/win/d3d11.py` + `native/win/d3d11` | full (WARP here) | yes | (LUT on GPU: later, see roadmap) |
| CPU compositor | `replacement/compositor.py` `CpuBackend` | full | yes | none |
| ONNX Runtime / DirectML / CPU | `ai/onnx_engine.py` (provider plan, silent-fallback label) | full | yes | real backend check (Phase 1) |
| YOLOX / YOLO11 / YOLO11-seg | `ai/onnx_engine.py` | full | yes | none |
| YuNet faces (+ head crops) | `ai/onnx_engine.py` `YuNetFaceDetector` | full; 5 landmarks are decoded but dropped | yes | keep landmarks, head pose (Phase 3) |
| PP-HumanSeg | `ai/person_segmentation.py` (background + per-person masks) | full | yes | quality modes (Phase 4) |
| OWLv2 + SAM 2.1 | `ai/open_vocab.py` (memory admission) | full, image mode only | yes | video propagation is not in the ONNX export (Phase 4 note) |
| ByteTrack | `ai/tracking.py` (continuous-time Kalman, two-stage) | full | yes | manual lock / history (Phase 4) |
| ROI editor, replacement | `roi/`, `replacement/`, `ui/roi_canvas.py` | full | yes | none |
| Background replacement | `replacement/background.py` (off / blur / image) | partial: no colour / gradient / video | yes | layers (Phase 4) |
| Resource scheduler | `core/scheduler.py` (priorities, step down/up, maintenance hold) | full | yes | new modules register here |
| Adaptive AI rate | `ai/adaptive.py` | full | yes | none |
| Scene manager, atomic state | `core/scene.py` (`SceneState`, slots, per-scene input) | full | yes | colour / layout per scene |
| Privacy mode | `replacement/privacy_mode.py` (faces / screens / all) | partial: classes only | yes | text regions, strict mode (Phase 5) |
| Auto-framing | `replacement/framing.py` | full | yes | none |
| Lighting | `replacement/lighting.py` (auto exposure, white balance) | full | yes | Color Studio comes after it |
| Hands, gestures | `ai/hands.py`, `ai/gestures.py` (21 landmarks, 5 gestures, actions) | full | yes | reused by automation |
| Recording, instant replay | `core/recorder.py` (bounded queue, h264_mf) | full | yes | audio effects chain |
| Audio recording | `core/audio.py` (DirectShow via PyAV, 48 kHz, host clock) | capture only | yes | DSP chain + meter (Phase 8) |
| Plugin SDK | `plugins/host.py` (SHA-256 approval, time budget, hang switch-off) | full | yes | none |
| Model import | `ai/model_import.py` | YOLO detect / seg only | yes | generic categories + report (Phase 1) |
| Model download | `ai/model_download.py` (pinned URLs, SHA-256) | full | yes | pose + text tables added |
| Hardware detection | `core/hardware.py` (tiers), `platform/win/sysinfo.py` | full except a real inference check | yes | backend smoke inference (Phase 1) |
| Action bus | `ui/actions.py` + `MainWindow.on_action` (hotkeys, tray, gestures) | full | yes | automation and remote control send actions here |
| Installer, update check, diagnostics | `packaging/windows`, `app/update_check.py`, `app/diagnostics.py` | full | yes | none |

## Not present (new in 0.6.0)

Multi-camera composition; body pose; face head-pose; OCR / text privacy; colour grading beyond the "colour"
region effect and auto lighting (no LUT, no grading controls); rule-based automation; audio DSP; remote control.

## Data flow with the 0.6.0 additions

```
main capture (CaptureSupervisor) ──► LatestSlot ──► VideoPipeline._iteration (one SceneState per frame)
secondary sources (multicam.SourceSet: a CaptureSupervisor each) ──► LatestSlot each (age-checked, never waited on)
   │
   ├─ lighting (existing) ─► Color Studio (new, profile "color") ─► multi-camera layout (new, profile "multicam")
   ├─ background (existing, +colour / gradient) ─► regions + privacy text boxes (compositor, existing)
   ├─ auto-framing (existing) ─► plugins (existing) ─► virtual camera + recorder taps
AI side threads, newest frame only, registered with the ResourceScheduler:
   detector + tracker (existing) · person masks (existing) · hands/gestures (existing)
   pose (new) · text regions (new, privacy) — results in LatestSlots, stale results dropped by timestamp
Events (people count, gestures, capture state, recording) ─► automation.RuleEngine (new, bounded, cooldowns)
   ─► action ids ─► the existing action bus (MainWindow.on_action) ◄── remote control API (new, localhost + token)
microphone (core/audio.py) ─► audio_fx chain (new: gate, EQ, compressor, AGC, limiter, meter) ─► recorder
```

Rules kept: the output thread never waits for AI, a file, the network or a model; every new AI module runs on its
own thread with a newest-frame slot and a rate the scheduler can lower; a failure in a new stage leaves the frame
as it was (same pattern as lighting / framing / plugins).
