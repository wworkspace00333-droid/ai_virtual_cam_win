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
| Models | use a registered model in the app: background segmentation (PP-HumanSeg layout) and privacy-shield text detection (PP-OCR DB layout), pose landmarks (MediaPipe pose layout), hand landmarks (MediaPipe hand layout, also for gestures), face detection (YuNet layout) and face mesh (MediaPipe face landmarker layout) via "Use in the app"; detectors / YOLO-seg via AI Engine → Import model; background also from 1-channel person maps and VOC / Cityscapes / ADE20K class maps (0.7.0); other semantic layouts checked & kept only | 🟡 | ai/model_check.active_path, AppContext.apply_custom_models | real models re-registered as custom; tamper / disabled fall back to built-in |
| Multi-camera | up to 3 extra sources (camera, screen/window, file, test pattern) | ✅ | core/multicam.py | synthetic (test pattern + bundled video) |
| Multi-camera | single, PiP, side by side, split, 2×2, custom tiles (crop, rotation, border, radius, opacity) | ✅ | core/multicam.py | synthetic (pixel tests) |
| Multi-camera | age-limited frames, hold / placeholder / hide, never waits | ✅ | core/multicam.SourceSet | synthetic |
| Multi-camera | transitions (0.7.0): cut / cross-fade / slide on primary or layout change | ✅ | multicam.transition_frame, MulticamService._transition | synthetic |
| Multi-camera | auto switch by people (hysteresis + cooldown); manual camera / layout hotkeys | ✅ | AutoSwitcher, actions | synthetic |
| Multi-camera | active-speaker switching: the source whose speaker's mouth moves clearly the most (face mesh lip gap, 1.5 s window), with the switcher's hold / cooldown | 🟡 | ai/speaker.py, AutoSwitcher "speaker" | real models on a synthetic talking / still close-up; real speech not measured |
| Vision | YuNet 5 landmarks kept; head pose (yaw / pitch / roll) | ✅ | ai/head_pose.py | synthetic round trip + real faces (bus.jpg) |
| Vision | body pose (39 points), posture, upper-body box | ✅ | ai/pose.py | real (bus.jpg) |
| Vision | hands: 21 landmarks reused from gestures (no second detector) | ✅ | ai/vision.py | synthetic |
| Vision | overlays, scheduler module (lowest priority), stale rejection | ✅ | ai/vision.py, VideoPipeline | synthetic pipeline |
| Vision | body gesture "hand_raised" (pose) as an automation trigger | ✅ | ai/vision.hand_raised, body_events | synthetic poses |
| Vision | body / face-aware auto-framing when the detector is off or blind | ✅ | ai/vision.framing_objects, VideoPipeline | synthetic |
| Vision | gaze direction (0.7.0): iris offset in the eye + head pose − direction to the camera; confidence (blink, head turn, mesh score), smoothing, stability, calibration | 🟡 | ai/gaze.py, VisionService._gaze | synthetic eyes of known geometry + real mesh on bus.jpg (irises between their corners); accuracy on real gaze not measured |
| Vision | eye contact correction (0.7.0): iris warp inside the eye opening, strength, fallback (blink / head turn / > 25° / mesh > 0.15 s old) | 🟡 | replacement/eye_contact.py, VideoPipeline._eye_contact | synthetic eyes (iris moves, outside pixels identical); 8 ms per face with 160 px eyes at 720p, CPU; naturalness on real faces not judged |
| Vision | dense face mesh (478 points incl. irises, up to 2 faces), overlay, face outline | ✅ | ai/face_mesh.py, ai/vision.py | real photo: agrees with YuNet (nose / mouth within 20-25 % of the face size; measured 1-4 px), rejects a wall; model provenance only partly documented |
| Background | colour, gradient, looping video layers | ✅ | replacement/background.py | synthetic + bundled clip |
| Background | chroma key (0.7.0): green / blue / picked colour, similarity, softness, spill suppression, feather; all background modes | ✅ | replacement/chroma_key.py, BackgroundService "chroma" | synthetic screen scene (shadow keyed, hair and skin kept, spill removed); 23.5 ms per 1080p matte |
| Background | Fast / Balanced / Quality (guided-filter edge refinement) | ✅ | ai/person_segmentation.guided_refine | synthetic edge test |
| Background | depth source: blur / replace what is farther than the focus (Depth Anything V2 Small, ≤ 4 maps / s) | ✅ | ai/depth.py, replacement/background.py | real photo: people nearer than the building; ~0.4 s per map on this CPU |
| Background | per-person masks / effects | ✅ (0.5.x) | PersonMaskDetector + per-object regions | — |
| ROI | keyframe animation of drawn rectangles (0.7.0): position, size, rotation, opacity; loop / ping-pong; easing; presets | ✅ | roi/animation.py, resolver(t), compositor opacity, roi_page | unit + compositor pixels + GUI |
| Tracking | manual lock, re-acquisition by position, 10 s buffer, track history | ✅ | ai/tracking.py | synthetic |
| Tracking | people list with Lock / Unlock; auto-framing of only the locked person(s) (choose who is framed) | ✅ | ui/studio/vision_tab.py, replacement/framing.py | synthetic + GUI test |
| Tracking | SAM 2.1 video propagation | ⛔ | ONNX export lacks memory modules | — |
| Tracking | mask propagation substitute (0.7.0): SAM on key frames, DIS optical flow in between, forward-backward + correlation checks force a new key frame | 🟡 | ai/mask_propagation.py, BoxMaskDetector | synthetic textured object (IoU > 0.85 / 0.8 after 1 / 2 carried frames, occlusion detected); 3.1 ms per carried mask; real video not measured |
| Privacy | faces / screens / background levels (existing) | ✅ | replacement/privacy_mode.py | existing tests |
| Privacy | text regions (PP-OCRv3), custom / excluded areas, padding, effects | ✅ | replacement/privacy_shield.py | real detector on synthetic text; misses documented |
| Privacy | "only sensitive text": CRNN recognition (16 MB, on click) + patterns (phone, card + Luhn, IBAN / long numbers, address, keywords; e-mail needs "@", which the English model cannot read) | ✅ | text_detect.TextRecognizer, privacy_shield | real models on a synthetic page: 4 sensitive lines hidden, plain text shown |
| Privacy | strict fail-safe (hold + grow; shield in passthrough; dark frame on shield error) | ✅ | privacy_shield, VideoPipeline._shield | synthetic |
| Privacy | faces hidden by their face-mesh outline (box when no mesh), padding, all effects | ✅ | privacy_shield.face_outlines, AppContext._face_outlines | synthetic + app test |
| Multi-camera | per-camera colour preset and privacy mode (shield / always hidden / not checked) | ✅ | MulticamService._grade, tile_filter mode, multicam_tab | synthetic + GUI |
| Privacy | extra Multi-Camera sources checked too (round robin, own boxes per source); strict: unchecked tile hidden whole; filter failure → placeholder | ✅ | privacy_shield.process_next_extra / apply_source, MulticamService.tile_filter | synthetic sources + compose |
| Privacy | ID cards / passports / plates as whole objects: four-cornered outline of card / document / plate proportions with text inside (photo on the card hidden too) | 🟡 | ai/document_detect.py, shield "documents" | real text detector on synthetic cards / plates at 0 / 12 / -25°; 0 false documents on 18 real photos; low-contrast edges missed (text still hidden) |
| Colour | virtual studio lights (0.7.0): key / fill / rim, direction, colour temperature, softness, presets; face-aimed, person-only with segmentation | 🟡 | replacement/virtual_light.py, VideoPipeline._relight, lights_card | synthetic maps and pixels; 28 ms per 1080p frame (2 cores); look on real faces not judged |
| Colour | exposure … sharpness, vignette, grain, glow, skin protection | ✅ | replacement/color.py | synthetic (values, direction) |
| Colour | .cube 1D / 3D import, intensity, per scene, presets | ✅ | replacement/color.py, color_tab | synthetic |
| Colour | GPU (D3D11) 3-D LUT pass: Texture3D, hardware trilinear, full resolution; used on hardware GPUs, CPU fallback | ✅ | native/win/d3d11 (lut_cs), replacement/color.py | correctness on WARP (error 0.19 vs exact, CPU path 3.3); speed on a hardware GPU not measured |
| Automation | 12 triggers, 9 actions, conditions, cooldown, debounce, priority, history | ✅ | core/automation.py | unit + app |
| Automation | AND / OR condition groups with "not": people, scene, object, recording, gaze, camera, time of day; text form in the rule builder | ✅ | core/automation.py parse_condition / group_ok, automation_tab | unit + GUI |
| Automation | gaze triggers (0.7.0): looks at / away from the camera, no fire on blink or lost face | ✅ | core/automation.py, AppContext._automation_snapshot | unit |
| Automation | loop protection, bounded queue, dedup, UI-thread execution | ✅ | automation + AppContext.call_main | unit |
| Audio | gain, mute, denoise, gate, EQ, compressor, AGC, limiter, meters | ✅ | core/audio_fx.py | synthetic signals (measured) |
| Audio | de-esser (Linkwitz-Riley split, sibilant-only reduction); presets Clean / Voice / Podcast / Noisy room | ✅ | core/audio_fx.py, audio_tab | synthetic tones (7.5 kHz reduced > 5 dB, 300 Hz and bright voice unchanged, flat crossover) |
| Audio | microphone monitoring (hear yourself), bounded queue | ✅ | core/audio_monitor.py (winmm waveOut) | real device opened here with silent blocks; fake device for the queue |
| Audio | system virtual microphone | ⛔ | needs signed kernel driver | reports/VIRTUAL_AUDIO_DEVICE.md |
| Video | AI handheld camera motion: 6 modes, 8 presets + own, object-aware pivot, auto intensity | ✅ | replacement/camera_motion.py, ui/studio/motion_tab.py | noise, no-loop autocorrelation, rigidity (SVD), no border, object margin, fades, real pipeline, GUI; OpenCL path untested (no device) |
| Scenes | transitions between scenes: cut / fade / slide / dip, per scene | ✅ | core/scene_transition.py, ui/pages/scenes_page.py | pure blends, state machine, real pipeline red→green fade, GUI |
| Remote | localhost HTTP API, WebSocket status, mobile page | ✅ | app/remote.py | real sockets |
| Remote | DPAPI token, auth, rate limit, lockout, host / origin checks, LAN opt-in | ✅ | app/remote.py | real |
| GUI | Studio page with 8 tabs, English + Russian | ✅ | ui/pages/studio_page.py, ui/studio/ | offscreen GUI tests |
