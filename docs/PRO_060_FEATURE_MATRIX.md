# 0.6.0 Professional — feature matrix

Status: ✅ implemented, in GUI + pipeline + tests · 🟡 partly (limits stated) · ⬜ not implemented · ⛔ not possible
without outside requirements. "Verified" says on what it was tested: **synthetic** (generated frames / signals, test
pattern, bundled clips and photos), **real** (real models on real photos, real HTTP sockets, real DPAPI), never on a
hardware GPU or a physical webcam (the development server has neither).

| Area | Feature | Status | Where | Verified |
|---|---|---|---|---|
| Hardware | tier profile (GPU / VRAM / cores / RAM / providers) | ✅ (0.5.3) | core/hardware.py | real (this server) |
| Hardware | real backend smoke inference per provider | ✅ | core/hardware.verify_backends | real: DirectML listed → ran on CPU (detected) |
| Hardware | driver version, Direct3D 11 feature level, Direct3D 12 support per adapter; a GPU without D3D12 is not counted for DirectML | ✅ | platform/win/sysinfo.py, core/hardware.py | real: Basic Render Driver 10.0.26100.33648, FL 12_1, D3D12 yes |
| Models | ONNX check for 9 categories, interface validation, dynamic shapes | ✅ | ai/model_check.py | real: 7 bundled models + wrong-category refusals |
| Models | per-provider sample inference, warm-up, median / p95 | ✅ | ai/model_check.py | real (CPU) |
| Models | SHA-256, licence metadata, registry, enable / disable, tamper check | ✅ | ai/model_check.py | real |
| Models | run any registered model inside the pipeline | 🟡 | detectors / YOLO-seg via model_import; others checked & kept only | — |
| Multi-camera | up to 3 extra sources (camera, screen/window, file, test pattern) | ✅ | core/multicam.py | synthetic (test pattern + bundled video) |
| Multi-camera | single, PiP, side by side, split, 2×2, custom tiles (crop, rotation, border, radius, opacity) | ✅ | core/multicam.py | synthetic (pixel tests) |
| Multi-camera | age-limited frames, hold / placeholder / hide, never waits | ✅ | core/multicam.SourceSet | synthetic |
| Multi-camera | auto switch by people (hysteresis + cooldown); manual camera / layout hotkeys | ✅ | AutoSwitcher, actions | synthetic |
| Multi-camera | active-speaker switching | ⬜ | no reliable per-camera speech signal | — |
| Vision | YuNet 5 landmarks kept; head pose (yaw / pitch / roll) | ✅ | ai/head_pose.py | synthetic round trip + real faces (bus.jpg) |
| Vision | body pose (39 points), posture, upper-body box | ✅ | ai/pose.py | real (bus.jpg) |
| Vision | hands: 21 landmarks reused from gestures (no second detector) | ✅ | ai/vision.py | synthetic |
| Vision | overlays, scheduler module (lowest priority), stale rejection | ✅ | ai/vision.py, VideoPipeline | synthetic pipeline |
| Vision | body gesture "hand_raised" (pose) as an automation trigger | ✅ | ai/vision.hand_raised, body_events | synthetic poses |
| Vision | body / face-aware auto-framing when the detector is off or blind | ✅ | ai/vision.framing_objects, VideoPipeline | synthetic |
| Vision | dense face mesh (468 points) | ⬜ | no model approved | — |
| Background | colour, gradient, looping video layers | ✅ | replacement/background.py | synthetic + bundled clip |
| Background | Fast / Balanced / Quality (guided-filter edge refinement) | ✅ | ai/person_segmentation.guided_refine | synthetic edge test |
| Background | depth-aware blur | ⬜ | no depth model approved | — |
| Background | per-person masks / effects | ✅ (0.5.x) | PersonMaskDetector + per-object regions | — |
| Tracking | manual lock, re-acquisition by position, 10 s buffer, track history | ✅ | ai/tracking.py | synthetic |
| Tracking | SAM 2.1 video propagation | ⛔ | ONNX export lacks memory modules | — |
| Privacy | faces / screens / background levels (existing) | ✅ | replacement/privacy_mode.py | existing tests |
| Privacy | text regions (PP-OCRv3), custom / excluded areas, padding, effects | ✅ | replacement/privacy_shield.py | real detector on synthetic text; misses documented |
| Privacy | sensitive patterns (e-mail, phone, card + Luhn, IBAN, address, keywords) | ✅ (matching) / 🟡 (needs recogniser model, not downloaded) | privacy_shield.sensitive_kinds | unit |
| Privacy | strict fail-safe (hold + grow; shield in passthrough; dark frame on shield error) | ✅ | privacy_shield, VideoPipeline._shield | synthetic |
| Privacy | ID cards / passports / plates as objects | ⬜ | no such classes; found only as text | — |
| Colour | exposure … sharpness, vignette, grain, glow, skin protection | ✅ | replacement/color.py | synthetic (values, direction) |
| Colour | .cube 1D / 3D import, intensity, per scene, presets | ✅ | replacement/color.py, color_tab | synthetic |
| Colour | GPU (D3D11) LUT path | ⬜ | CPU only, measured | — |
| Automation | 12 triggers, 9 actions, conditions, cooldown, debounce, priority, history | ✅ | core/automation.py | unit + app |
| Automation | loop protection, bounded queue, dedup, UI-thread execution | ✅ | automation + AppContext.call_main | unit |
| Audio | gain, mute, denoise, gate, EQ, compressor, AGC, limiter, meters | ✅ | core/audio_fx.py | synthetic signals (measured) |
| Audio | microphone monitoring (hear yourself), bounded queue | ✅ | core/audio_monitor.py (winmm waveOut) | real device opened here with silent blocks; fake device for the queue |
| Audio | system virtual microphone | ⛔ | needs signed kernel driver | reports/VIRTUAL_AUDIO_DEVICE.md |
| Remote | localhost HTTP API, WebSocket status, mobile page | ✅ | app/remote.py | real sockets |
| Remote | DPAPI token, auth, rate limit, lockout, host / origin checks, LAN opt-in | ✅ | app/remote.py | real |
| GUI | Studio page with 8 tabs, English + Russian | ✅ | ui/pages/studio_page.py, ui/studio/ | offscreen GUI tests |
