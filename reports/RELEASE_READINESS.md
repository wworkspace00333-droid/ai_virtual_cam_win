# Release readiness — 2026-11-19

## 1. How the readiness figure is calculated

Unit of counting: the 115 feature rows of `docs/WINDOWS_FEATURE_MATRIX.md` (the project's own list of everything the
Windows version claims, grouped A–I), with each row's status as updated by today's runs. A row counts as
**confirmed** only if it is implemented, wired into the app and confirmed by a passing test or a live run on this
machine. Nothing is counted for existing code alone.

| Status | Rows | Share |
|---|---|---|
| Confirmed (READY, incl. 15 rows completed during the 0.5 plan) | 94 | **81.7 %** |
| Partial (works with important limits) | 9 | 7.8 % |
| Implemented, cannot be verified here (GPU, Zoom, Teams, Discord, Camera app, VRAM, edge quality, GPU compositor) | 8 | 7.0 % |
| Blocked (code signing; browser camera access in this environment) | 2 | 1.7 % |
| Not implemented (zero-copy CPU↔GPU; CUDA/TensorRT — deliberately not shipped) | 2 | 1.7 % |

**Functionality confirmed by tests or live runs: 94 of 115 = 82 %.** This is a measure of what was *proven on this
machine*, not of quality on users' PCs: every GPU path and every third-party video app is in the unverified part.

## 2. Test totals (today)

- Full suite: **617 passed, 0 failed, 40 skipped** (skips: macOS-only code, OBS not running, camera-modifying tests).
- Live + stress run (overlaps some files of the full suite): **70 passed, 0 failed, 16 skipped**.
- Runtime checks: self-check 17/18 (screen capture warning), camera loopback ok, full chain to camera ok (also with
  shipped models only), 9 resolution/rate configurations ok, fault isolation 3 experiments, 20-min soak ok, 60-min soak
  (see FULL_PROJECT_AUDIT §7).

## 3. Remaining work

### P0 — blocks a release for general users

| Task | Status | Complexity | Depends on | Files | Done when | Confirming tests |
|---|---|---|---|---|---|---|
| Verify the update fix (0.4.1/0.5.0 → 0.5.1 with browsers open) | **verified after the audit with 0.5.2**: update over 0.4.1 with Chrome and Claude holding the camera DLL succeeded, camera registered, installed self-check 13/13; forced-abort path still only tested by compilation | S | publishing or downloading the draft | `packaging/windows/installer.iss` | silent update succeeds with Chrome/Claude open; camera registered afterwards; forced abort re-registers the old camera | manual run here; add a CI step that holds the camera DLL open during an update |
| Real PC with a GPU: `windows_pc_report`, 1–4 h soak, camera in Zoom / Teams / Discord / Chrome, colour | not started (no hardware) | S + fixes | owner's / tester's PC | `docs/HARDWARE_CHECKLIST.md` | checklist filled; fps/latency/VRAM on GPU; each client works | pc-report folder, checklist |
| Code signing (installer, camera DLLs) | script ready, no certificate | S + external | certificate purchase | `scripts/sign_win.ps1`, CI secrets | `Get-AuthenticodeSignature` Valid | CI check |

### P1 — stability, compatibility, performance

| Task | Status | Complexity | Files | Done when | Tests |
|---|---|---|---|---|---|
| Plugin time budget: a plugin whose average exceeds the frame budget is switched off (or run every n-th frame) | **done after the audit** (25 ms average / 1 s single call; tests in `test_plugins.py`) | S | `vcstudio/plugins/host.py` | output stays ≥ 55 fps with a 40 ms plugin; user sees why it was switched off | `fault_isolation.py` turned into `test_plugins` case |
| Plugin hang protection (watchdog detects a plugin call longer than N s, disables plugins, restarts output) | **done after the audit** (`PluginManager.stalled`, watchdog) | M | `plugins/host.py`, `core/watchdog.py` | a `time.sleep(60)` plugin does not freeze the camera > 2 s | new test |
| Resource admission for OWLv2/SAM (RAM, no-GPU warning) | **done after the audit** for RAM (2.5 / 1.5 GB free required; SAM silhouettes fall back to people); VRAM still open | S | `ai/open_vocab.py`, `ui/pages/detection_page.py` | clear refusal/warning on low RAM; no OOM | test with fake hardware profile |
| Physical webcams: MF/DirectShow on 3+ models, MJPG/YUY2/NV12, USB reconnect | blocked (no hardware) | M | `platform/win/capture.py`, `native/win/capture` | webcam matrix filled | `VCS_LIVE=1` on that PC |
| Colour BT.709 vs BT.601 decision | waiting for client tests | S–M | `native/win/vcam/vcam_frames.cpp` | same colour in Zoom/Teams/OBS | colour test |
| Fresh-profile model choice: start from the first-run measurement instead of the largest model | **done after the audit** (models scaling > 3× over budget skipped) | S | `ai/engine.py`, `ai/auto_profile.py` | no yolox_s phase on a 2-core PC | e2e test on fresh profile |
| Remove the temp data folder after `--soak`, `--pipeline-e2e-test`, `gui_e2e.py`, `gui_soak.py` (like D21) | **done after the audit** (`core/paths.throwaway_home`) | S | `app/soak.py`, `app/selftest.py`, `scripts/gui_e2e.py`, `scripts/gui_soak.py` | no `vcs-*` folder left after each mode | extend `test_p0_robustness.py` |
| Handle growth check (+11/h over 20 min) | inconclusive | S | — | 60-min/4-h soak slope ≈ 0 | soak (60-min result in FULL_PROJECT_AUDIT) |

### P2 — quality and function

| Task | Status | Files | Done when |
|---|---|---|---|
| Hardware Compatibility Layer steps 1–4 (profile, provider verification, admission, budget-aware choice) | partly done after the audit: silent provider fallback labelled (step 2), RAM admission (step 3), first-run baseline for the model choice (step 4); hardware profile (step 1) open | new `core/hardware.py`, `ai/onnx_engine.py`, `ai/auto_profile.py` | unit tests with fake adapters pass; profile in diagnostics |
| Face accuracy on video (turns, profile, glasses, distance); threshold for backs of heads | partial (photos only) | `ai/onnx_engine.py` | numbers in `docs/ACCURACY.md` from annotated video |
| Silhouettes for classes other than people without SAM | partial (people built in) | `ai/segmentation.py`, models | permissive instance-segmentation model shipped (download/licence decision) |
| Open vocabulary completion (stronger model, 20 annotated videos) | partial | `ai/open_vocab.py` | accuracy + speed on GPU |
| Manual GUI click-through on a real desktop | not done in this audit | `ui/*` | checklist of all controls ticked |

### P3 — later

| Task | Files |
|---|---|
| Owner check for the OBS shared-memory section (reader side) and an explicit DACL in the OBS plugin | `platform/win/obs_shm.py`, `native/obs-plugin/vcstudio-obs.c` |
| Zero-copy GPU readback into the camera slot (D8) | `platform/win/d3d11.py`, `vcam_writer.py` |
| VRAM monitoring feeding the watchdog | `platform/win/gpu_monitor.py`, `core/watchdog.py` |
| Plugins out of process (crash/leak isolation) | `plugins/` |

## 4. What cannot be verified without other hardware or computers

DirectML inference and Direct3D 11 on a real GPU (NVIDIA, AMD, Intel), VRAM use and device-loss recovery on hardware,
physical webcams and USB reconnect, Zoom / Teams / Discord / browsers / Windows Camera app, colour per client,
SmartScreen behaviour with a signed build, Windows 10 machines, low-RAM (8 GB) machines.

## 5. Verdict

- **As a preview for testers: yes.** On a CPU-only machine the full chain works at 1080p60 with AI, recovers from
  injected faults, does not leak memory over 20 min, and all runnable tests pass.
- **As a release for general users: not yet.** Three blockers: (1) the update path removed the camera on this server —
  fixed in source, not yet proven with a real update; (2) nothing has run on a GPU, a physical webcam or a
  third-party video app; (3) unsigned installer and camera DLLs.

First steps, in order: verify the 0.5.1 update fix here (needs the owner's go-ahead to publish or download the draft);
add the plugin time budget (small, measurable); run `HARDWARE_CHECKLIST.md` on a PC with a GPU and a webcam.
