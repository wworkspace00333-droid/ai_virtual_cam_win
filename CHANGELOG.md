# Changelog

All notable changes to this project. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html): while the version is 0.x, a minor
release may change settings or profiles (they are migrated automatically); 1.0.0 comes after the hardware checklist
(docs/HARDWARE_CHECKLIST.md) passes on real PCs.

## [Unreleased]

Fixes from the 0.5 audit (docs/WINDOWS_AUDIT_0.5.md, P0).

### Fixed
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

[0.4.1]: https://github.com/wworkspace00333-droid/ai_virtual_cam_win/releases/tag/v0.4.1
[0.4.0]: https://github.com/wworkspace00333-droid/ai_virtual_cam_win/releases/tag/v0.4.0
