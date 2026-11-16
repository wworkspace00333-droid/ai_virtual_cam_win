# Contributing — AI Virtual Camera Studio (Windows)

The program is proprietary (`LICENSE`); the OBS plugin in `native/obs-plugin` is GPL-2.0-or-later. Contributions are
accepted only with the owner's agreement. This file describes how to work on the Windows port.

## Set up

```bat
:: Python 3.12 x64; the venv must sit at a SHORT path (PySide6 contains paths longer than 260 characters)
py -3.12 -m venv C:\vcs-env
C:\vcs-env\Scripts\python -m pip install -r requirements-dev-windows.txt
C:\vcs-env\Scripts\python scripts\fetch_models.py          :: YOLOX + YuNet, SHA-256 checked
powershell -ExecutionPolicy Bypass -File scripts\build_native_win.ps1 -Test   :: needs VS 2022 Build Tools (C++ x64)
C:\vcs-env\Scripts\pre-commit install                      :: ruff, mypy, licence gate, no large files
C:\vcs-env\Scripts\python -m vcstudio                      :: run the app
```

## Work rules

- Branch `windows-port`, small commits, each with a clear message. Pushing to GitHub happens only with the owner's
  consent, and only to the Windows repository (`ai_virtual_cam_win`).
- Do not delete the macOS implementation; Windows code lives in `vcstudio/platform/win` and `native/win`, shared code
  picks the platform through `vcstudio/platform`.
- No network code, telemetry or automatic updates in the app unless behind a separate, documented setting.
  Developer scripts may download (models, licence texts) with a pinned source and checksum.
- No invented numbers: performance or accuracy figures in docs come from a script run and name the machine.
  Anything that needs real hardware (GPU, webcam, Zoom/Teams/Chrome/Discord, clean Windows 10/11) is marked as not
  verified and listed in `docs/HARDWARE_CHECKLIST.md`.
- Thread lifecycle: a worker thread owns what it reads (capture device, mapped view) and closes it itself; restarts give
  the new worker its own stop event (`docs/AUDIT_WIN.md`, R1–R9).

## Before every commit

```bat
C:\vcs-env\Scripts\ruff check .
C:\vcs-env\Scripts\mypy
C:\vcs-env\Scripts\python -m pytest -q
```

Coverage (the core packages must stay at or above 80 % on a full local run):

```bat
C:\vcs-env\Scripts\python -m pytest -q --cov --cov-report=json:build/coverage.json
C:\vcs-env\Scripts\python scripts\coverage_gate.py
```

Live tests run only when their hardware or software is there: the camera tests need the installed "AI Virtual Camera";
`VCS_LIVE_OBS=1` runs the OBS chain (OBS 30+ with the plugin; the scripts start and close OBS themselves);
`VCS_LIVE_RENAME=1` re-registers the camera (elevated shell); `VCS_STRESS=1` runs the stress tests.

## Style

- ruff (`pyproject.toml`): line length 120 as the target, 140 hard limit; short statements on one line are fine.
- mypy on `vcstudio/platform` and `vcstudio/core`.
- macOS-only functions in shared modules carry `# pragma: macos` (excluded from the Windows coverage figure).
- User-visible strings go through `tr()` and get a Russian entry in `vcstudio/i18n_ru.py`; texts that differ by OS
  go into `vcstudio/platform/_TEXT`.

## Licences

- Allowed in the installer: MIT, BSD, Apache-2.0, Zlib, ISC, PSF, CC0; LGPL only as separate, replaceable DLLs (Qt,
  FFmpeg). Not allowed: GPL / AGPL code or models (Ultralytics YOLO, YOLO-World, YOLOE, YOLO-NAS weights, x264, x265,
  GPL-only Qt modules). The only GPL part that ships is the separately licensed OBS plugin, with its source.
- A new dependency, model or asset needs: its licence checked (code and weights separately), an entry in
  `THIRD_PARTY_NOTICES.md`, and `scripts/check_licenses.py` passing. Models are added to `SHIPPED_MODELS`
  (`packaging/vcstudio_win.spec`) and `scripts/fetch_models.py` with their SHA-256.
- The installer build (`scripts/build_installer_win.ps1`) runs the licence gate before and after PyInstaller and fails
  on a violation; PyAV must be the LGPL build (`scripts/build_pyav_lgpl_win.ps1`).

## CI

- `.github/workflows/ci-windows.yml` — every push / pull request: ruff, mypy, native build + native test, pytest with
  coverage, coverage gate (threshold from the repository variable `VCS_COVERAGE_MIN`, default 70 until the CI value
  is known), licence gate for information.
- `.github/workflows/installer-windows.yml` — manual run and `v*` tags: LGPL PyAV, OBS plugin, installer, artifact.
  Signing from the secrets `VCS_SIGN_PFX_BASE64` / `VCS_SIGN_PFX_PASSWORD` (`scripts/sign_win.ps1`).
- GitHub's Windows runners have no camera, GPU or Media Foundation virtual camera use; those tests skip there.
