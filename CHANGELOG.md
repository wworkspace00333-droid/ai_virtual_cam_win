# Changelog

All notable changes to this project. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html): while the version is 0.x, a minor
release may change settings or profiles (they are migrated automatically); 1.0.0 comes after the hardware checklist
(docs/HARDWARE_CHECKLIST.md) passes on real PCs.

## [Unreleased]

## [0.6.0-preview] — 2026-11-19

0.6.0 Professional, preview build for testing on real PCs (a pre-release, not offered by "Check for updates" in
0.5.x). Everything below is in the new **Studio** page (8 tabs, English
and Russian) and runs through the existing pipeline, scheduler, scene state and action bus. Details, measurements and
limits: docs/PRO_060_*.md and reports/PRO_060_*.md.

### Added
- **AI Models**: a compatibility check for any ONNX model in 9 categories (interface validated against the category,
  sample inference on every execution provider with the provider really used, warm-up / median / p95, SHA-256,
  licence metadata), a registry of checked models with enable / disable and a tamper check. **Check backends** runs a
  small model per provider: on the development server DirectML is listed but falls back to the CPU, and now says so.
- **Multi-Camera**: up to 3 extra sources (camera, screen / window, video file, test pattern), each with its own
  capture supervisor; layouts single, picture-in-picture, side by side, split, 2×2 grid and custom tiles (crop,
  rotation, border, rounded corners, opacity); age-limited frames with hold / placeholder / hide; automatic switching
  when nobody is on the main camera (hysteresis + cooldown); hotkeys for next camera / next layout.
- **AI Vision**: YuNet's five face landmarks are kept and give head pose (yaw / pitch / roll); body pose (MediaPipe
  person detector + 39-point landmarker, downloaded on click, 17 MB); hand skeletons reused from gesture control (no
  second detector); overlays on the output; a lowest-priority scheduler module.
- **Background**: colour, gradient and looping-video backgrounds; Fast / Balanced / Quality masks (Quality refines
  the edge against the picture with a guided filter).
- **Tracking**: lock an object (kept through 10 s of occlusion, re-acquired by position), track history.
- **Privacy Shield**: local text detection (PP-OCRv3, 3 MB download) hides text regions, plus the user's own areas and
  exclusions; pixelate / blur / black; padding and sensitivity; sensitive-text patterns (e-mail, phone, card with
  Luhn check, IBAN-like, address, keywords) for use with a recognition model; strict fail-safe mode.
- **Color Studio**: exposure, brightness, contrast, saturation, temperature, tint, gamma, highlights (tone mapping),
  shadows, sharpness, vignette, film grain, soft glow, skin protection; .cube LUTs (1D and 3D) with intensity; per
  scene, applied once per frame; presets.
- **Automation**: rules with 12 triggers (people, objects, faces, gestures, camera lost, recording, timer, hotkeys,
  manual), conditions, 9 actions, cooldown, debounce, priority, history and loop protection.
- **Audio Studio**: noise suppression, gate, 3-band EQ, compressor, automatic gain, limiter and live meters for the
  microphone of recordings (about 1.4 % of one core with everything on). Virtual microphone feasibility:
  reports/VIRTUAL_AUDIO_DEVICE.md (needs a signed driver; not shipped).
- **Remote Control**: localhost HTTP API, WebSocket live status and a phone page; off by default, DPAPI-protected
  access code, rate limits, lockout, host / origin checks, LAN only by explicit opt-in.

### Changed
- Automation and remote-control actions run on the UI thread (profile listeners update widgets).
- Gesture listeners also get gestures that have no direct action (automation rules can use them).

## [0.5.3] — 2026-11-19

Hardware profile and an OBS shared-memory owner check.

### Added
- A hardware profile of the PC (`core/hardware.py`): hardware GPUs and their video memory, cores, RAM, the ONNX
  Runtime providers that can help, and a tier (gpu, gpu-low-vram, cpu, cpu-low). It is written to the start-up log
  and to the diagnostics ZIP, so a report from a user's PC says what the app saw. Software adapters (WARP, the
  Basic Render Driver) are never counted as a GPU.
- The AI Engine page shows what this PC is (graphics card for AI / AI on the processor, cores, RAM), and turning
  on "any object by text" on a PC without a usable graphics card says at once to expect seconds per frame.

### Security
- The OBS direct-frame reader reads only a shared-memory section owned by this user (or administrators / SYSTEM);
  before, any process in the session could create a section with that name and feed the app frames.

## [0.5.2] — 2026-11-19

Fixes from the 2026-11-19 audit (reports/). Includes the 0.5.1 installer fix (0.5.1 was not published).

### Fixed
- **A slow plugin no longer drags the camera down.** Plugins run on the output thread; one taking 40 ms per frame
  held 1080p60 at 20 fps and stayed on. A plugin whose average exceeds 25 ms per frame, or one frame over 1 s, is
  now switched off with the reason shown; a call that never returns is switched off by the watchdog before the
  output restarts.
- The soak, pipeline self-test and GUI test modes never removed their temporary data folder (170-280 MB each, mostly
  the model cache); dozens of them had filled the development server's disk. They are now removed on exit.
- "Any object by text" (OWLv2, +SAM 2.1) and SAM silhouettes check free memory first (about 2.5 and 1.5 GB were
  measured) and refuse with a message instead of pushing the PC into swap; silhouettes then fall back to the
  built-in person segmentation.
- A fresh profile no longer starts on a model the PC clearly cannot run: the first-run measurement of the smallest
  model, scaled by model size, skips models far over the speed mode's budget (on the 2-core server: straight to
  yolox_nano instead of several seconds on yolox_s at 190 ms per frame).
- When a GPU provider was requested but ONNX Runtime silently ran the model on the CPU, the AI backend now reads
  "CPU (DirectML unavailable)" and the log says so.

## [0.5.1] — 2026-11-19

Fixes updating over an installed version.

### Fixed
- **Updating could leave the PC without its camera.** The installer asked Windows to close every app that had the
  camera's files open — and any browser, Zoom or messenger that lists cameras has them open. It cannot close those,
  so a silent update stopped, after it had already removed the installed camera. Now only the app itself is
  closed (the camera files are replaced after a restart, as before), and an update that stops for any reason
  registers the previous camera again.
- `--camera-loopback-test`, `--pipeline-e2e-test` and `--capture-selftest` without their report path print a usage
  line instead of failing with IndexError.

## [0.5.0] — 2026-11-19

The first full release of the Windows version: every feature of the 0.5 plan is in (docs/WINDOWS_ROADMAP_0.5.md).
No functional changes since 0.4.8.

### In this version (since 0.4.0)
- Inputs: webcams (Media Foundation, DirectShow, shared mode), OBS, a screen or window, a video file with pause and
  seeking; output to the app's own camera (Media Foundation and DirectShow, several apps at once).
- AI on this PC: YOLOX objects, YuNet faces (distant faces too), person silhouettes, background blur and
  replacement, optional "any object by text" (OWLv2 + SAM 2.1); a resource scheduler keeps the camera smooth and
  runs the detector only when something needs it.
- Scenes with atomic switching, a different replacement for each object, privacy mode, auto-framing, lighting,
  gestures, recording and instant replay, Python plugins, update check on request.

### Known limitations
- Developed and tested on a server without a graphics card (software Direct3D): speed on real GPUs, physical
  webcams and colour in each video app are not verified yet (docs/HARDWARE_CHECKLIST.md).
- The installer is not code-signed: SmartScreen warns once.
- "Any object by text" is slow without a GPU (seconds per frame); silhouettes of things other than people need
  SAM 2.1 or an imported model.

## [0.4.8] — 2026-11-19

Silhouettes of people without a download; Hide face finds distant faces.

### Added
- **Object (silhouette) replacement of people works out of the box.** Without a segmentation model the app cut
  people out with SAM 2.1 (a separate download, ~4 s per frame without a graphics card) or not at all; now each
  "person" box from the detector is segmented with the shipped PP-HumanSeg model (the one behind background blur):
  70 ms per frame for four people including the detector on the development server, clean outlines (bags and the
  bus behind them stay out). Other classes still use SAM 2.1 or an imported segmentation model; the Replacement
  page says which.
- **Hide face finds distant faces.** Faces are searched on a 640 px copy of the frame, where a face under ~30 px
  in 1080p (a person a few metres away) was missed and stayed uncovered. The head of every small person the
  detector sees is now searched again on its own enlarged crop: a 14 px face in a 1080p test frame — 0 of 2
  found before, 2 of 2 now; a few ms per frame, and no extra work for people whose face is already found.

## [0.4.7] — 2026-11-18

Camera output crash fixed; CPU compositor 4-5x faster.

### Fixed
- A crash (access violation) when the camera output was closed while a frame was being written to it: the
  virtual-camera writer now finishes the frame first and ignores frames sent after closing. This was the cause
  of the occasional crash of the full test run.

### Changed
- The colour effect (brightness, contrast, saturation) on the CPU compositor is one 8-bit OpenCV pass
  (`cv2.transform`): the soak's three regions 32 -> 13 ms per 1080p frame, identical result.
- **The CPU compositor (used when Direct3D is not available or fails) is 4-5x faster.** It blends with 8-bit
  OpenCV calls straight into the output instead of float32 NumPy over every box, and a wide blur region runs on a
  smaller copy (as the background blur already did). 1080p, two regions (logo with mask, video): 68 -> 15 ms per
  frame; with a blur and a pixelate region too: 114 -> 20 ms. Live 1080p60 with regions on the CPU compositor:
  13 -> 55 fps. Results differ from before by at most 1 level (blur: 3). Software Direct3D (WARP) uses the same
  faster blur for blur regions.

## [0.4.6] — 2026-11-18

Background blur 2-3x cheaper on the CPU; maintenance jobs isolated from the live video; shared webcams shown on the Input page.

### Added
- The Input page says when a webcam is shared with another app ("shared with another app"): the resolution then is
  the one that app chose. README and Troubleshooting describe camera sharing on Windows.

- Maintenance isolation (roadmap P2.1): the detector benchmark, accuracy check, model import check, the
  first-start performance measurement, model downloads and saving an instant replay run in below-normal
  priority threads, the CPU-heavy ones one at a time. While one runs, the resource scheduler holds instead of
  throttling the live AI for a temporary dip (Performance page: "maintenance job running").

### Changed
- **Background blur and replacement are 2-3x cheaper on the CPU.** Frames between two mask updates (the mask
  changes 5-15 times a second, frames 60) reuse its upscaled blend weights, a wide blur runs on a 1/8-size copy
  (52-54 dB PSNR against the previous 1/4-size blur: no visible difference), and the blend writes straight into
  the output. Per 1080p frame on the 2-core development server: blur 17 -> 7.4 ms, image 9.3 -> 3.3 ms; live
  1080p60 with blur and the camera output on: 44.8 -> 55-56 fps.

## [0.4.5] — 2026-11-18

Several apps on one camera; core test coverage 87 % (83.5 % in CI); CLIP measured (not enabled).

### Added
- **Several apps on one camera.** Media Foundation capture of a camera another app already controls (a webcam
  in use by Zoom, for example) now joins it in shared mode instead of failing with "in use"; both apps get the
  frames, in the mode the controlling app chose. The app's own Media Foundation camera works the same way for
  other apps: apps that open cameras in shared mode run alongside each other and alongside one app with full
  control. Measured on Windows build 26100 with three readers at once, 30 fps each. This needed no newer
  Windows SDK (the sensor-group API is in SDK 22621), so the planned SDK 26100 download was not needed.
  Two apps that both want full control of the Media Foundation camera still cannot share it: the DirectShow
  camera remains the way for those.

### Tests
- Core coverage 83.9 → 87.1 % locally: the ONNX model importer, the elevated camera setup call (UAC outcomes),
  the GPU counters (a stand-in pdh.dll answering like Windows) and the GDI screenshot are now tested on any
  machine. macOS-only code paths in shared modules moved into functions marked `# pragma: macos`, like the rest
  of the macOS code; behaviour on Windows is unchanged.

## [0.4.4] — 2026-11-17

Microphone sound in recordings and instant replay.

### Added
- **Sound in recordings and instant replay** (Settings → Recording → Microphone): the chosen microphone is
  recorded as AAC next to the video, through FFmpeg's DirectShow input and its own AAC encoder in the LGPL build
  (no new package). Sound and picture use the same clock; the microphone runs only while a recording or the
  instant replay needs it. Drift between the microphone's and the PC's clock is corrected before it reaches
  40 ms (lip-sync errors are noticed from ~45 ms). Checked with generated sound: a tone and a white frame at the
  same moment land within 0.1 ms of each other in the file. The camera itself still carries no sound (Windows
  cameras are video only); video apps take the microphone directly. Not tried with a real microphone yet: the
  development server has none.

## [0.4.3] — 2026-11-17

Gesture control and a faster picture on PCs without a graphics card (background, lighting).

### Added
- **Gesture control** (P2.7, Settings → Gestures): open palm, fist, victory, pointing finger and thumbs up, held
  for about 0.6 s in front of the camera, run an action of your choice — the same actions as the hotkeys (by
  default: open palm → privacy mode, victory → save instant replay). A gesture fires once and again only after
  the hand changed. MediaPipe palm detector + 21 hand landmarks (OpenCV Zoo ONNX, Apache-2.0, ~8 MB, downloaded
  on your click, SHA-256 checked); recognised on the input picture at 8 fps, ~30 ms per frame on the 2-core
  server. Checked on a COCO photo: two hands found, a flat hand recognised as an open palm, a hand holding
  scissors as no gesture. Off by default.

### Changed
- Background blur / replacement on a PC without a graphics card is about 1.7–2× cheaper: the edge remap runs
  on the 192×192 mask instead of every output pixel (1080p blur 26 → 15 ms, image 17 → 8.5 ms per frame; at most
  a few levels of difference on the person's edge). Live at 1080p60 with blur on the 2-core server: output
  33.8 → 48.1 fps (D27).
- Lighting is about 6× cheaper: ~6 ms per 1080p frame on the 2-core server instead of ~40 (one pass with a
  4-channel lookup table, metering on the shrunk whole frame — no full-frame copies).
- "Start with Windows" follows a reinstalled app: an entry pointing to a removed exe or another install folder is
  rewritten at start (D13).
- CI actions on Node 24 (checkout v7, setup-python v7, upload-artifact v7, cache v5).

## [0.4.2] — 2026-11-17

The fixes from the 0.5 audit (docs/WINDOWS_AUDIT_0.5.md) and most of the 0.5 roadmap (docs/WINDOWS_ROADMAP_0.5.md):
scenes, privacy mode, auto-framing, lighting, recording, instant replay, plugins. Still tested only on a server without
a graphics card.

### Fixed
- Tests: every test app wrote its own copy of the optimised AI models (30–80 MB each), about 1.5 GB per full run,
  kept for three runs — enough to fill this server's disk. The test apps now share one cache per run.
- An AI model loads even when its optimised copy cannot be saved (full disk, read-only cache folder); before, ONNX
  Runtime's "Protobuf serialization failed" left the AI in an error state (D23).
- The self-check, the diagnostics ZIP and the camera loopback test delete their temporary folders; every self-check
  used to leave about 270 MB in %TEMP% (D21). The benchmark tool cleans up too.
- Low disk space (< 500 MB) is shown in the status bar and recorded once; a full event journal no longer breaks the
  watchdog or shutdown (D22).
- Texts no longer promise CUDA: the Windows build ships ONNX Runtime with DirectML and CPU only (D19). The 0.4.0 notes
  below said "CUDA" — that was not accurate.

- Maximum Quality no longer runs the detector at ~2 fps: under load its rate was scaled from the already throttled
  rate and ratcheted down to the floor; it now scales from the unthrottled rate (D26).

### Added
- **Plugin SDK** (P3.3, API version 1, `docs/PLUGIN_SDK.md`, Settings → Plugins): your own Python effects on the
  camera output, one `.py` file each in the plugins folder; each frame comes with the tracked objects. Listing
  plugins never runs their code (name and API version are read from the syntax tree); a plugin runs only after
  you switch it on and confirm, and the approval is bound to the file's SHA-256 (a changed file is off again);
  an exception or a wrong result leaves the frame unchanged, three in a row switch the plugin off; emergency
  passthrough bypasses plugins; `VCS_NO_PLUGINS=1` starts without any. Example: `examples/plugins/vignette.py`.
- **Check for updates** (P3.5, Settings → Updates): one request to this project's GitHub releases, only when you
  click; nothing about you is sent, nothing is downloaded or installed, the app never checks by itself; off with
  `VCS_NO_NETWORK=1` or `privacy.allow_update_check: false`.
- The installer build in CI installs the built installer silently and runs the PC report from the installed app
  (`--quick --strict`) before attaching it to a release (P3.4, D9).
- **Recording** (P3.1, Preview page "● Record", Ctrl+Alt+F9): the camera output — exactly what apps receive — is
  written to an MP4 in Videos\AI Virtual Camera Studio. H.264 through the Windows Media Foundation encoder (hardware
  where the graphics card offers it), no x264; falls back to NVENC / Quick Sync / AMF and finally MPEG-4. The output
  thread only queues a copy (a frame that does not fit is dropped and counted), so recording never slows the camera;
  frame times are kept (a pause stays a pause). On the 2-core server: 90 frames of 720p encoded in 0.55 s.
- **Instant replay** (P3.2, Preview page, Ctrl+Alt+F10 saves): while on, the last 30 s (5–300, setting
  `recording.replay_seconds`) are kept in memory as downscaled JPEG frames, about 2 MB per second at 720p; nothing
  touches the disk until you save.
- **Lighting enhancement** (P2.6, Preview page): automatic exposure metered on the inner part of the faces (a bright
  window behind no longer darkens the face), grey-world white balance, highlight roll-off instead of clipping,
  smoothed over time; applied to the camera picture before the background and replacements, so user images stay as
  they are. A dimmed, tinted COCO photo: face brightness 0.18 → 0.35. Found by the tests: metering on a padded face
  box measured mostly background and darkened a backlit face — now it meters inside the face.
- **Auto-framing** (P2.5, Preview page): the output is cropped to the people in view (with the output's aspect ratio,
  zoom up to 2×) and follows them smoothly; small movements are ignored (dead zone with hysteresis, so a zoom-out
  still reaches the full frame); after 2 s without anyone it eases back to the full picture. It is the last step of
  the output, so regions and replacements stay where they belong.
- **Privacy mode** (P2.4, Preview page selector, Ctrl+Alt+H): Off · Hide faces · Hide faces and screens (monitors,
  laptops, phones, books — their screens or pages can show private information) · Hide faces, screens and
  background. It adds its own regions to the scene and remembers the background it replaced, so switching it off
  restores the scene as it was; the user's regions are untouched. Checked on COCO photos: a booklet, books and a
  monitor pixelated.
- **A different picture for each object** (P2.3): on an Automatic / Hybrid region, "A different asset for each
  object (in turn)" gives the 1st tracked object the main asset, the 2nd the first checked extra, and so on, wrapping
  round; an object keeps its asset while it is tracked, a new scene hands them out afresh.
- **Scenes** (P2.2, `vcstudio/core/scene.py`, new "Scenes" page): a scene is a profile (regions, replacements,
  background, AI) plus a hotkey slot (Ctrl+Alt+1…9; without explicit slots, list order) and, optionally, its own input
  (a camera, a file or a screen). **Atomic scene state:** the app builds one immutable snapshot of everything a frame
  needs (regions, replacement specs, assets, background with its decoded image) after the new scene is loaded and
  publishes it with a single assignment; the output thread takes that one reference per frame, so a frame is always
  entirely one scene. Before, regions, replacements and background were swapped one after another and a frame in
  between could mix them. Test: 12 switches between a red and a green scene with video flowing — every output frame
  one solid colour matching the scene recorded in it. Scene cards show a thumbnail of the scene's last output.
- **Resource scheduler** (P2.1, `vcstudio/core/scheduler.py`): one governor for the optional heavy modules. When the
  output falls below the input rate, the least important active module slows first — background segmentation
  (15 → 5 fps), then detection (down to 25 %) — and the most important recovers first when there is headroom. The
  Performance page has an "AI resources" panel (module, priority, rate, fps, cost, state, reason); every step is
  recorded in the journal. Live on the 2-core server with background blur + people + face hiding at 1080p60: both
  modules reached their floors within 21 s as designed, but the output stayed at 21–28 fps — the remaining cost is
  blending the background into every 1080p frame on the CPU (≈ 20 ms), which needs a graphics card.
- Video file input: start position, Pause and "From the start" buttons, position readout (P1.6).
- Replacement by silhouette without a segmentation model: when SAM 2.1 is installed (Detection page → Download
  models) and no segmentation model is, the detector's boxes get SAM 2.1 masks — only for the classes the silhouette
  regions use, at most 4 objects per frame (P1.2, D6). On this server's CPU: 4 people in 3.9 s per frame — it works,
  but following movement live needs a graphics card.

### Changed
- The CI installer build uses a pinned FFmpeg (dated BtbN build + SHA-256) instead of the daily "latest" (D3).
- AI on demand: when no region follows detected objects (only manual regions, or none), the detector runs at a
  2 fps standby rate — previews still show boxes, the model stays loaded, the CPU/GPU go to the video (P1.1, D20).
- The input method is chosen on the Input page only; Settings shows it with a link (P1.7, D17).
- Tests: the GUI tests with a detector run on Windows with YOLOX (they waited for a macOS model); macOS-only live
  tests are skipped on Windows (P1.8, D24).

## [0.4.1] — 2026-11-16

### Added
- **Video file as the input** (Input → Method → Video file): any MP4 / MOV / MKV / WebM / AVI clip is processed like
  a camera, in real time at the clip's frame rate, looping or holding the last picture, and sent to the output
  camera. Checked live: a 30 fps clip with face hiding reached the app's camera at 29.7 fps (Media Foundation
  client through Frame Server) and 30.5 fps (DirectShow client).
- Virtual Camera page: how to pass the processed video on as "OBS Virtual Camera" (OBS Video Capture Device →
  this app's camera → Start Virtual Camera).

## [0.4.0] — 2026-11-16 — first Windows release (preview)

The first release of the Windows port. Developed and tested on Windows Server 2025 without a graphics card; numbers
for real hardware are still to be measured (docs/HARDWARE_CHECKLIST.md).

### Added
- **Windows platform:** the app runs on Windows 10 (1809+) and 11 alongside the unchanged macOS code: paths in
  `%LOCALAPPDATA%`, single instance, system information, global hotkeys (RegisterHotKey), UTF-8 file handling.
- **Own virtual camera "AI Virtual Camera":** a Media Foundation camera (Windows 11, served by Frame Server) and a
  DirectShow camera (Windows 10/11); both installed by default on Windows 11; install, reinstall, rename and remove
  from the app (one UAC prompt). A waiting card is shown to apps before the first frame and while paused.
- **Capture:** Media Foundation (own native reader) and DirectShow webcams, OBS Virtual Camera, direct frame
  transfer from OBS through the vcstudio-obs plugin, and **a screen or a single window** (Windows Graphics Capture,
  with DXGI Desktop Duplication and GDI as fallbacks).
- **AI on ONNX Runtime** (CUDA, DirectML, CPU) with an optimised-model cache; YOLOX nano / tiny / s detectors
  (Apache-2.0), YuNet faces (MIT), PP-HumanSeg person segmentation (Apache-2.0).
- **Any object by text (optional):** OWLv2 detection by description and SAM 2.1 masks by box or click; the models
  (~220 MB) are downloaded only when you click "Download models" on the Detection page, each file SHA-256 checked.
- **Background blur and replacement** behind people, on the GPU compositor where a graphics card is present.
- **Direct3D 11 compositor** with CPU fallback and recovery after a GPU device loss.
- **One click on the Preview page:** Hide face, Logo (new), Card for a game (screenshots through Windows Graphics
  Capture).
- **Performance:** automatic speed mode chosen on first start from a short measurement; the AI rate adapts when the
  output cannot keep up (down to 25 %, back up when there is headroom).
- **Everyday use:** first-run wizard (language, system check, source, mode), tray icon (closing the window keeps the
  camera running), start with Windows (current user, minimised), diagnostics ZIP with user, computer, home folder and
  e-mail removed, English and Russian interface.
- **Installer** (PyInstaller + Inno Setup): licence page, clean upgrade, uninstall that removes the cameras and the
  autostart entry; optional Authenticode signing of every own binary and the installer through `VCS_SIGN_*`
  environment variables; SHA-256 file next to the installer.
- **Quality:** ruff, mypy (platform and core), pytest with a coverage gate, pre-commit hooks, GitHub Actions CI on
  Windows Server 2025, licence gate (`scripts/check_licenses.py`) for the package closure, FFmpeg build, models and
  the built app folder.
- Documentation: release audit (docs/AUDIT_WIN.md), accuracy report (docs/ACCURACY.md), hardware checklist,
  CONTRIBUTING.md, THIRD_PARTY_NOTICES.md.

### Changed
- **Licence:** proprietary; free to install and use, source published for reference (LICENSE). The OBS plugin is
  released under GPL-2.0-or-later with its source shipped alongside.
- Default detector is YOLOX instead of Ultralytics YOLO (AGPL-3.0); YOLO models can still be imported by the user.
- PyAV is built against an LGPL-only FFmpeg (no x264 / x265); the sample photo is a public-domain image.
- Windows wording everywhere on Windows (no Core ML / macOS references).

### Fixed
- Thread lifecycle: each capture, AI and OBS reader thread owns its device and stop event, so a restart can no
  longer close a device another thread is reading; camera shared memory is created with a restrictive DACL and an
  existing section from another user is refused (audit R1–R9).
- Desktop Duplication no longer takes its empty first frame as a black picture.

### Known limitations
- Not code-signed yet: SmartScreen warns on first run.
- Media Foundation camera in shared mode (two apps at once on the MF camera) needs Windows SDK 26100; meanwhile the
  second app can use the DirectShow camera.
- Not yet measured on real hardware: GPU speed, colour in each video app (MF camera BT.709, DirectShow BT.601),
  games captured in full screen. See docs/HARDWARE_CHECKLIST.md.
- "Any object by text" takes seconds per frame on a CPU; it is meant for PCs with a graphics card.

## [0.3.2] and earlier

macOS releases; see the history of the original repository.

[0.6.0-preview]: https://github.com/wworkspace00333-droid/ai_virtual_cam_win/releases/tag/v0.6.0-preview
[0.5.3]: https://github.com/wworkspace00333-droid/ai_virtual_cam_win/releases/tag/v0.5.3
[0.5.2]: https://github.com/wworkspace00333-droid/ai_virtual_cam_win/releases/tag/v0.5.2
[0.5.1]: https://github.com/wworkspace00333-droid/ai_virtual_cam_win/releases/tag/v0.5.1
[0.5.0]: https://github.com/wworkspace00333-droid/ai_virtual_cam_win/releases/tag/v0.5.0
[0.4.8]: https://github.com/wworkspace00333-droid/ai_virtual_cam_win/releases/tag/v0.4.8
[0.4.7]: https://github.com/wworkspace00333-droid/ai_virtual_cam_win/releases/tag/v0.4.7
[0.4.6]: https://github.com/wworkspace00333-droid/ai_virtual_cam_win/releases/tag/v0.4.6
[0.4.5]: https://github.com/wworkspace00333-droid/ai_virtual_cam_win/releases/tag/v0.4.5
[0.4.4]: https://github.com/wworkspace00333-droid/ai_virtual_cam_win/releases/tag/v0.4.4
[0.4.3]: https://github.com/wworkspace00333-droid/ai_virtual_cam_win/releases/tag/v0.4.3
[0.4.2]: https://github.com/wworkspace00333-droid/ai_virtual_cam_win/releases/tag/v0.4.2
[0.4.1]: https://github.com/wworkspace00333-droid/ai_virtual_cam_win/releases/tag/v0.4.1
[0.4.0]: https://github.com/wworkspace00333-droid/ai_virtual_cam_win/releases/tag/v0.4.0
