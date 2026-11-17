# Changelog

All notable changes to this project. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html): while the version is 0.x, a minor
release may change settings or profiles (they are migrated automatically); 1.0.0 comes after the hardware checklist
(docs/HARDWARE_CHECKLIST.md) passes on real PCs.

## [Unreleased]

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

[0.4.3]: https://github.com/wworkspace00333-droid/ai_virtual_cam_win/releases/tag/v0.4.3
[0.4.2]: https://github.com/wworkspace00333-droid/ai_virtual_cam_win/releases/tag/v0.4.2
[0.4.1]: https://github.com/wworkspace00333-droid/ai_virtual_cam_win/releases/tag/v0.4.1
[0.4.0]: https://github.com/wworkspace00333-droid/ai_virtual_cam_win/releases/tag/v0.4.0
