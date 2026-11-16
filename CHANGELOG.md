# Changelog

All notable changes to this project. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html): while the version is 0.x, a minor
release may change settings or profiles (they are migrated automatically); 1.0.0 comes after the hardware checklist
(docs/HARDWARE_CHECKLIST.md) passes on real PCs.

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

[0.4.0]: https://github.com/wworkspace00333-droid/ai_virtual_cam_win/releases/tag/v0.4.0
