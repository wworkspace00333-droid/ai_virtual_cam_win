# AI Virtual Camera Studio for Windows — full project audit, 2026-11-19

Scope: the local repository (branch `windows-port` @ `f317b65`, equal to `win/main`, tag v0.5.1; published releases
0.4.7, 0.4.8, 0.5.0), run and measured on this machine. Audit mode: no refactoring, no commits; the four files in
`reports/` are the only changes. Companion reports: [TEST_RESULTS.md](TEST_RESULTS.md),
[HARDWARE_COMPATIBILITY_REPORT.md](HARDWARE_COMPATIBILITY_REPORT.md), [RELEASE_READINESS.md](RELEASE_READINESS.md).

Status words used below: **verified live** (run on this machine end to end), **verified by tests** (automated
tests pass here), **partial**, **implemented, not verified**, **not implemented**, **cannot be verified here**.

## 0. Before the audit (disclosure)

- The working tree was clean. The last two commits (`0cdb432`, `f317b65` + tag v0.5.1) were pushed earlier today,
  before the audit request arrived.
- Shortly before the audit, a silent update 0.4.1 → 0.5.0 on this server **failed and left the virtual camera
  unregistered** (§10). During the audit the camera was registered again with the installed 0.4.1's own
  `vcam-setup.exe --install --both` (returned 0; status: DirectShow and Media Foundation registered). No data was
  deleted. The audit removed only its own temporary files (a downloaded installer and an old test extraction).

## 1. Architecture (from source)

Python 3.12 application (PySide6 UI) with native Windows parts in C++:

```
capture (MF / DirectShow / screen WGC+DXGI+GDI / video file / OBS shared memory / test source)
   │  newest frame only: LatestSlot (core/frame_queue.py)
   ├──────────────► AI engine thread (ai/engine.py): detector (YOLOX ONNX), faces (YuNet), silhouettes
   │                 (PP-HumanSeg / SAM 2.1), open vocabulary (OWLv2), hands; results → LatestSlot; tracker
   │                 predicts boxes to each output frame's timestamp
   ├──────────────► background segmentation thread (PP-HumanSeg, own LatestSlot)
   ▼
video pipeline thread (core/video_pipeline.py): scene snapshot (core/scene.py, one reference per frame) → ROI
resolve → framing / lighting / privacy → compositor (Direct3D 11 native DLL, WARP or GPU; CPU fallback) →
plugins → camera writer (shared memory, seqlock, 3 slots) → AI Virtual Camera (vcam_mf.dll via Frame Server,
vcam_dshow.dll in client apps) ; recorder / instant replay (bounded queues)
supervisors: capture_supervisor (reconnect), watchdog (stalls, memory), scheduler (AI budget), maintenance (heavy jobs)
```

Size: `vcstudio/` 25,800 lines of Python in 133 files (largest: core 4,992; ai 4,207; platform/win 3,105;
replacement 2,046; app 1,464; UI pages and widgets), native Windows C/C++ ≈ 3,000 lines without the DirectShow
baseclasses (capture, d3d11, vcam, setup tool, tests). 74 test files, 555 test functions, 657 collected test items.

## 2. Module audit

| Module | Implementation | Dependencies | Real implementation? | Tests | Status | Limits / risks found |
|---|---|---|---|---|---|---|
| Frame pipeline | `core/video_pipeline.py` | NumPy, compositor | yes | test_pipeline, test_reliability, test_stress | **verified live** (60 fps chain to camera client) | heaviest load on 2 cores: p05 42 fps |
| LatestFrameSlot | `core/frame_queue.py` `LatestSlot` | threading.Condition | yes; overwrites unconsumed item, counts `overwritten` | test_capture, test_pipeline | **verified by tests** | — |
| Queues | recorder `Queue(maxsize)`, replay deque trimmed by time, video assets ≤ 8 frames, hotkey call queue unbounded (UI calls only) | — | bounded where frames flow | test_recorder, test_video | **verified (code + tests)** | no unbounded frame queue found |
| Scene Manager / atomic scene | `core/scene.py`, `ui/pages/scenes_page.py` | — | immutable snapshot published by one assignment | test_scenes (incl. "no mixed frame under load") | **verified by tests** | — |
| Multi-object replacement | `roi/resolver.py`, `replacement/spec.py` | tracker IDs | yes | test_multi_object | **verified by tests** | — |
| ROI + tracking | `roi/*`, `ai/tracking.py` (ByteTrack-style, Kalman) | NumPy | yes | test_roi, test_tracking, test_ai | **verified by tests** | — |
| YOLOX | `ai/onnx_engine.py` `OnnxYoloxDetector` | ONNX Runtime | yes | test_ai, test_accuracy (mAP floors on coco16) | **verified live** (§4) | auto choice starts at yolox_s (190 ms) before stepping down |
| Faces (YuNet) | `ai/onnx_engine.py` `YuNetFaceDetector` | OpenCV DNN | yes, + head crops for distant faces | test_quick_face(_win), test_box_masks | **verified live** on photos; head turns on video **not verified** | 2 false positives on backs of heads (D25) |
| Person segmentation | `ai/person_segmentation.py` | ONNX Runtime | yes (background + per-person silhouettes) | test_background, test_box_masks | **verified live** | hair/fine detail quality not measured |
| OWLv2 + SAM 2.1 | `ai/open_vocab.py` | ONNX Runtime, downloads 220 MB | yes | test_open_vocab, test_box_masks | **verified live, slow**: 7.6 s/frame (OWLv2), 12 s with SAM | ~3–4 GB memory growth; no RAM admission check |
| Background replacement | `ai/person_segmentation.py` `composite_background`, `replacement/background.py` | OpenCV | yes | test_background | **verified by tests + benchmark** | — |
| Privacy mode | `replacement/privacy_mode.py` | faces, detector, background | yes | test_privacy_mode | **verified by tests** | coverage on real video not measured on Windows |
| Auto-framing | `replacement/framing.py` | tracker | yes | test_framing | **verified by tests** | — |
| Lighting | `replacement/lighting.py` | faces | yes | test_lighting | **verified by tests** | — |
| Gestures | `ai/gestures.py`, `ai/hands.py` (MediaPipe palm + landmarks, download 8 MB) | ONNX Runtime | yes | test_gestures | **verified by tests**; 15 ms/frame measured | no real hand video tested |
| AI scheduler | `core/scheduler.py`, `ai/adaptive.py`, `core/maintenance.py` | — | yes | test_scheduler, test_adaptive_rate, test_maintenance, test_ai_on_demand | **verified by tests + live** (AI hang does not touch output, §6) | GPU load not measurable here |
| Compositor D3D11 | `platform/win/d3d11.py` + `native/win/d3d11` | D3D11 | yes (WARP here) | test_metal (array path), test_replacement_win | **verified on WARP**; GPU **cannot be verified here** | WARP render ≈ 13–19 ms per 1080p frame |
| Compositor CPU | `replacement/compositor.py` `CpuBackend` | OpenCV | yes | test_cpu_compositor | **verified by tests** | — |
| Virtual camera (MF + DirectShow) | `native/win/vcam/*`, `platform/win/vcam_writer.py`, `camera/output.py` | Frame Server, COM | yes | test_vcam_win (MF client, DShow client, several apps) | **verified live** with the app's own clients | Zoom/Teams/Discord/browsers **not tested**; not code-signed |
| Capture | `platform/win/capture.py`, `native/win/capture`, `screen_capture.py`, `video_file_source.py`, `obs_shm.py` | MF, DirectShow, WGC | yes | test_capture_win, test_screen_capture, test_video_file_source | **verified on virtual devices**; physical webcam **cannot be verified here** | — |
| Recording / instant replay | `core/recorder.py` | PyAV (LGPL FFmpeg), MF H.264 | yes | test_recorder | **verified by tests** | — |
| Plugin SDK | `plugins/host.py`, `docs/PLUGIN_SDK.md`, `examples/plugins/vignette.py` | importlib | yes | test_plugins | **verified by tests + live** | **slow plugin blocks output** (§6, §8) |
| GUI | `ui/*` 13 pages, ≈ 186 controls | PySide6 | yes | 56 UI test functions (offscreen) | **partially verified** | not every control is exercised by a test (§9) |
| Installer | `packaging/windows/installer.iss`, `scripts/build_installer_win.ps1` | Inno Setup 6, PyInstaller | yes | CI: build, silent install, installed self-check | **clean install verified in CI**; **update over 0.4.1 failed here** (§10) | not code-signed |
| Update check | `app/update_check.py` | GitHub API, opt-in | yes | test_update_check | **verified live** (0.4.8 sees 0.5.0) | — |
| Logging / diagnostics | `core/logging_setup.py`, `app/diagnostics.py` | — | yes, redacts user/computer/home/e-mail | test_tray_diagnostics | **verified by tests** | — |
| Crash recovery | `core/watchdog.py`, `capture_supervisor.py`, D3D device recreation, crash-session detection in `core/database.py` | — | yes | test_reliability, soak fault injection | **verified live** (soak: 32/32 faults recovered) | device loss on real GPU not verified |

Stubs: no TODO/FIXME in Python or Windows native code. The three `NotImplementedError` are abstract base classes
(`CaptureSource`, `Grabber`). No decorative-only pages were found in source: every page's controls are wired to
settings, profile or context actions (spot-checked; full click-through not automated, §9).

## 3. Hardware

See HARDWARE_COMPATIBILITY_REPORT.md. Key facts: 2 cores, 31 GB, no hardware GPU, Direct3D 11 on WARP, DirectML
listed but sessions run on the CPU (verified with `get_providers()`), app correctly labels "CPU". Disk C: has
2.4 GB free — a risk for long logs and recordings on this machine.

## 4. AI engine measurements (CPU, sample photo 1280×863; RAM = process RSS growth while loading and running)

| Module | Load s | Provider | Avg ms | P50 | P95 | FPS | RSS growth MB | VRAM | Result on photo | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| yolox_nano | 0.15 | CPU | 23.8 | 23.7 | 24.9 | 42.0 | 52 | n/a (no GPU) | 4 people, bus, suitcase, backpack, handbag | OK |
| yolox_tiny | 0.10 | CPU | 54.4 | 54.3 | 55.9 | 18.4 | 52 | n/a | 5 people, 3 buses, … | OK |
| yolox_s | 0.12 | CPU | 190.3 | 189.4 | 193.1 | 5.3 | 119 | n/a | 5 people, 3 buses, truck, … | OK, slow on 2 cores |
| YuNet (frame pass) | 0.01 | OpenCV CPU | 23.3 | 23.2 | 23.8 | 43.0 | 17 | n/a | 2 faces | OK |
| YuNet, 1080p frame, faces 40 / 24 / 14 px | — | OpenCV CPU | 20.7 / 20.7 / 30.7 | — | 21.1 / 21.1 / 31.7 | — | — | n/a | frame pass 2 / 2 / **0**; with head crops 2 / 2 / **2** | OK (distant faces via head crops) |
| PP-HumanSeg 192×192 | 0.02 | CPU | 17.5 | 17.4 | 17.7 | 57.1 | 20 | n/a | person share 13 % | OK |
| yolox_nano + PP-HumanSeg silhouettes | 0.03 | CPU | 67.7 | 67.6 | 69.0 | 14.8 | 23 | n/a | 4 people, 4 masks | OK |
| yolox_nano + SAM 2.1 silhouettes | 0.35 | CPU | 3580 | 3521 | 3714 | 0.3 | 1434 | n/a | 4 masks | works, impractical on CPU |
| OWLv2 ("person", "bus") | 0.86 | CPU | 7713 | 7616 | 7913 | 0.13 | 2820 | n/a | 8 people, 3 buses | works, **not real-time** on CPU |
| OWLv2 + SAM 2.1 | 1.21 | CPU | 11736 | 12103 | 12103 | 0.09 | 4232 | n/a | 11 masks | works, not real-time |
| Hands (palm + landmarks) | — | CPU | 15.0 | 14.7 | 16.7 | 66 | — | n/a | photo has no visible hands: 0 | runs; accuracy not measured |

RSS growth for the later rows includes earlier models still held by ONNX Runtime arenas, so it is an upper bound
per module, not an exact per-model footprint. Video-based checks (head turns, face disappearing/reappearing, moving
people, hair edges) were **not** possible: no camera and no annotated video on this machine; tracking continuity
is covered only by unit tests (stable IDs, coasting, re-identification).

## 5. Video pipeline and virtual camera

Path verified live: test source → capture → pipeline → AI → D3D11 (WARP) → shared memory → AI Virtual Camera (MF,
Frame Server) → MF client (the app's own test client; `--pipeline-e2e-test`): 60 fps, 11–12 ms latency, replacement
present in 100 % of client frames; same with the shipped models only.

Per resolution and rate (project test source, AI on, people pixelated + logo; camera output off — camera formats
were measured separately through Frame Server):

| Input | Out fps avg | Out fps min (1 s) | Latency p95 max ms | Process ms | Render errors | AI model / fps |
|---|---|---|---|---|---|---|
| 640×480 @15 | 14.0 | 11 | 8.8 | 4.2 | 0 | yolo11n* / 11.5 |
| 640×480 @30 | 29.9 | 30 | 4.9 | 3.2 | 0 | yolox_nano / 20 |
| 640×480 @60 | 59.7 | 58 | 4.3 | 2.9 | 0 | yolox_nano / 20 |
| 1280×720 @15 | 15.0 | 15 | 8.4 | 5.9 | 0 | yolox_nano / 15 |
| 1280×720 @30 | 29.9 | 29 | 7.9 | 6.4 | 0 | yolox_nano / 20 |
| 1280×720 @60 | 59.3 | 57 | 16.3 | 6.6 | 0 | yolox_nano / 17 |
| 1920×1080 @15 | 15.0 | 14 | 16.5 | 11.7 | 0 | yolox_nano / 15 |
| 1920×1080 @30 | 30.0 | 30 | 14.8 | 12.7 | 0 | yolox_nano / 15 |
| 1920×1080 @60 | 57.4 | 47 | 26.3 | 13.1 | 0 | yolox_nano / 13.5 |

\* first configuration ran while the auto choice was still stepping down (yolo11n is a development model present
on this machine, not shipped).

Code review of the frame path: newest-frame-only slots between threads; AI and background segmentation never block
the pipeline (they publish into slots); the camera writer holds a lock only around the shared-memory copy (the D10
crash fix); threads have stop events and joins; capture reconnects with back-off. Camera loopback: 1080p, client
61 fps, frame order correct.

## 6. AI scheduler and fault isolation (live)

| Experiment | Result |
|---|---|
| Baseline 1080p60 with AI | 55.5 fps |
| Detector made to hang 20 s per call | output **59.4 fps** while hung; 60.2 after; pipeline restarts 0 |
| Plugin that raises every frame | switched off after 3 failures (`state=error`), output 59.8 fps |
| Plugin that takes 40 ms per frame | output **19.7 fps**; plugin stays `running`, only flagged `slow` |
| Heavy jobs (benchmark, accuracy check, downloads) | serialised and the scheduler holds AI changes while they run (tests) |
| Modes | performance / balanced / low_resource / max_quality all implemented; benchmark 51.9–59.6 fps avg output in every mode and scenario |

Observation: while the detector was hung, `ai.stats().state` stayed "running" for the 8 s window — the AI stall
watchdog acts on a longer threshold; the output was unaffected, which is the requirement.

## 7. Stability

| Run | Result |
|---|---|
| 20 min soak (heavy profile, fault injection every ~37 s, MF client reading 60 fps) | 32/32 faults recovered (max 0.77–1 s), memory 766 → 769 MB (max 1194 during a fault), output median 60 / p05 42 fps, client median 60, 0 render/AI errors, 0 restarts, handles 943–1074 (slope +11/h, inconclusive over 20 min) |
| 60 min soak (same profile and fault injection) | 96/96 faults recovered (max ≈ 1.0 s), memory 764 → 780 MB (slope +2 MB/h, max 1231 during a fault), threads 31–42 (slope −0.5/h), handles 935–1068 (slope −3.6/h: the +11/h of the 20-min run was noise), 0 render/AI errors, 0 restarts, camera 152,732 frames sent / 0 dropped, client median 60 / p05 59 fps. **Output median 42 / p05 29 fps is not representative**: during this run the auditor ran test suites (incl. OWLv2 at 7 s/frame) on the same 2 cores; the clean 20-min run gave median 60 / p05 42 |
| Stress tests (24 ROIs, profile churn, input flapping, many players, …) | 7/7 pass |
| Scene switching / module toggling / camera disconnect | covered by stress + soak fault types (input stall, freeze, disconnect, AI fault, render fault) |
| Resolution / FPS switching | covered by the 9-configuration matrix (restart per configuration) |

## 8. Security

| Area | Finding | Severity |
|---|---|---|
| Camera shared memory | DACL: this user full, LOCAL SERVICE read, app containers read, restricted read, Low integrity no-write-up; refuses a pre-existing section owned by another user (`vcam_writer.py`) | OK |
| OBS shared memory | the OBS plugin creates `Local\vcs-obs-frames` with the default DACL; the app reader validates header, sizes and strides but does not check the owner — a process in the same session could feed frames | low (P3) |
| Network | only two outbound calls: model downloads (fixed huggingface.co URLs, SHA-256 checked, temp file) and the update check (fixed GitHub API URL, timeout, opt-in, `VCS_NO_NETWORK`); no listening sockets | OK |
| Plugins | listed by AST without running code; enabled one by one, approval bound to the file's SHA-256 (changed file → off); errors isolated. But plugins run **in-process on the output thread**: a slow plugin lowers the camera fps (measured), a hung plugin would stop the output, a native crash or memory leak in a plugin affects the whole app | **medium (P1)** |
| Installer | admin rights only for camera registration; uninstall keeps user data; **update failure removed the camera** (§10) | **high (P0, fixed in source)** |
| Temp files | self-check and benchmark remove their temp folders (D21 tests). **But `--soak` (`app/soak.py:61`), `--pipeline-e2e-test` (`app/selftest.py:199`), `scripts/gui_e2e.py` and `scripts/gui_soak.py` never remove their `vcs-*` data folder** (170–280 MB each, mostly the optimised-model cache). On this server 55 such folders plus pytest's own temp dirs had filled `%TEMP%` to 11 GB and left C: with 1.5 GB free; removing them (except the running soak's) freed 10.5 GB | medium (P1, same class as D21) |
| Diagnostics | ZIP redacts user name, computer name, home, data folder, e-mails; no assets | OK |
| Code signing | installer and camera DLLs unsigned (SmartScreen warning) | P0.7 (needs certificate) |

## 9. GUI and UX

13 pages (Dashboard, Input, Preview, AI Engine, Detection, ROI Editor, Replacement, Profiles, Scenes, Virtual
Camera, Performance, Logs, Settings) with ≈ 186 interactive controls in source. Automated offscreen UI tests
(56 functions) render every page in English and Russian, drive the main actions (hide face, accuracy check, ROI
editing, scenes, first run, tray, diagnostics, camera page states) and check settings persistence. Not every control
has a test; a manual click-through on a real desktop was not done in this audit (no simulated clicks on the owner's
RDP desktop). Missing models, missing camera and device loss show explicit messages (tests for missing segmentation
model, YuNet missing, camera not installed).

## 10. Installer and update

| Check | Result |
|---|---|
| Build + silent install + installed self-check | CI success for v0.5.0 and v0.5.1 |
| Published installer integrity | 0.5.0: SHA-256 of the download equals the `.sha256` file |
| **Update 0.4.1 → 0.5.0 (silent, as a user would update)** | **failed**: Restart Manager listed Claude and Chrome as using the camera DLLs (any app that enumerates cameras loads them), could not close them, and the silent install aborted (exit 5) — *after* `PrepareToInstall` had unregistered the installed camera. Result: 0.4.1 still installed, **camera gone**. Log kept in the scratchpad |
| Fix | in source and tag v0.5.1 (`f317b65`): `CloseApplicationsFilter=vcstudio.exe` (camera DLLs already use `restartreplace`), and `DeinitializeSetup` re-registers the previous camera if the update stops early. Compiled with ISCC against a stub; **not yet verified with a real update** (0.5.1 release is an unpublished draft) |
| Uninstall / reinstall | not run (would remove the installed camera; needs the owner's consent) |
| User settings across update | by design kept in `%LOCALAPPDATA%\AIVirtualCameraStudio`; not re-verified today |

## 11. Performance bottlenecks (measured)

| # | Where | Cause | Measured impact | Possible solution | Risk | Expected gain | How to verify |
|---|---|---|---|---|---|---|---|
| 1 | `platform/win/d3d11.py` `D3D11Context.render` (WARP) | software rasteriser on 2 cores | 11.7–13.1 ms per 1080p frame (process ms), up to 19 ms under load | none on CPU besides fewer layers; a real GPU removes it | — | 10+ ms on GPU PCs | benchmark on a GPU PC (P0.1) |
| 2 | `ai/onnx_engine.py` `cpu_threads` | AI limited to half the physical cores (1 thread here) to protect output | PP-HumanSeg 17.4 ms in-app vs 6.0 ms with all cores | keep (it protects 60 fps); optionally scale with measured headroom | output stalls if raised blindly | 2–3× AI rate on idle CPUs | soak with the change |
| 3 | `ai/engine.py` auto model choice | budget modes start at the largest shipped model and step down | first seconds at yolox_s (190 ms, 2.5 AI fps) in fresh profiles | start from the first-run measurement / stored hardware profile | low | instant correct model | e2e test on a fresh profile |
| 4 | `ai/open_vocab.py` | OWLv2 base + SAM on CPU | 7.6–12 s per frame, +3–4 GB RAM | warn / refuse without GPU or enough RAM; smaller model (download) | low | avoids unusable state | test with a fake hardware profile |
| 5 | `plugins/host.py` `run` | plugins synchronous on the output thread | 40 ms plugin → 55 to 20 fps | per-plugin time budget: switch off (or skip frames) when average exceeds the frame budget | low | output protected | the audit's `fault_isolation.py` as a test |
| 6 | `platform/win/vcam_writer.py` `publish` | BGRA copy into shared memory | 0.6 ms per 1080p frame | not worth changing without GPU readback (D8) | — | ≤ 0.6 ms | — |
| 7 | heaviest profile on 2 cores | AI + output + client compete | p05 42 fps in soak (median 60) | scheduler lever for "background quality"; GPU | medium | fewer dips | soak |

Python overhead and GUI: the pipeline thread's own work at 1080p is 2.9–13 ms including WARP render; no Python
hot spot above the items listed was found in this run.

## 12. Findings summary

Critical (P0): update failure removing the camera (fixed in source, unverified); no code signing; nothing verified
on a GPU, physical webcam or third-party video app. Important (P1): slow plugins block output; open vocabulary has no
resource admission; OBS shared-memory reader has no owner check (P3). Details and the task list: RELEASE_READINESS.md.
