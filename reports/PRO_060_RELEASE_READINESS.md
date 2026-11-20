# 0.6.0 Professional — release readiness (2026-11-19)

**Verdict: not ready to publish as 0.6.0 final. Published as previews (0.6.0-preview, 0.6.0-preview2) for testing
on real PCs.** The code is complete for the scope in
docs/PRO_060_FEATURE_MATRIX.md and the automated checks pass, but several release requirements of the master prompt
are not verified on real hardware yet.

## Requirements (master prompt, part 18)

| # | Requirement | Status | Evidence / gap |
|---|---|---|---|
| 1 | Every announced feature really implemented | ✅ for the ✅ rows of the feature matrix; ⬜ / ⛔ rows are not announced as done | feature matrix |
| 2 | No broken old features | ✅ automated | 760 / 0 / 40 full suite, 71 / 0 live + stress |
| 3 | No critical regressions | ✅ automated | the 4 first-run failures were intended expectation changes |
| 4 | Camera output stable | 🟡 | all 21 pipeline runs within 2 % of input rate (synthetic); the colour stage needed a frame-rate guard (fixed); no Zoom / Teams run with the new features |
| 5 | AI errors isolated | ✅ | every new stage falls back to the unmodified frame; vision / shield / extra sources on own threads; tests with failing analysers |
| 6 | Old profiles migrate correctly | ✅ automated | new sections are optional and default to off; settings deep-merged; old-profile tests pass |
| 7 | New settings persist | ✅ | GUI tests write through settings / profiles |
| 8 | Installer updates safely | 🟡 | preview installer built and installed fresh in CI with the installed-app check passing; an upgrade over 0.5.3 is not tested yet (the same installer code passed 0.4.1 → 0.5.2) |
| 9 | Uninstall leaves nothing | ⬜ for 0.6.0 | no new system components were added (no driver, no service, no firewall rule); new data files live in app data |
| 10 | Model and dependency licences checked | ✅ | 3 new Apache-2.0 models in THIRD_PARTY_NOTICES; no new Python dependencies |
| 11 | New APIs protected | ✅ | reports/PRO_060_SECURITY.md, tests/test_remote.py |
| 12 | Documentation current | ✅ | README, CHANGELOG (Unreleased), ARCHITECTURE, HARDWARE_CHECKLIST, PRO_060_* docs and reports |

## Open before a final 0.6.0 (state 2026-11-20, after 0.6.0-preview2)

Published pre-releases: v0.6.0-preview and **v0.6.0-preview2** (installer 227 MB + .sha256; CI build, silent install
and installed-app check passed). 0.5.3 stays the latest release, so 0.5.x users are not offered the previews.

1. Upgrade over an installed version: tried on this server (installed 0.5.2) — the installer stops safely in
   PrepareToInstall (exit code 8) because a reboot is still pending from the earlier 0.4.1 → 0.5.2 update
   (vcam_dshow.dll was in use); 0.5.2 and both cameras stayed intact. Needs a reboot of the server (the owner's
   decision; this session runs on it), then: install preview2 silently, installed self-check, Studio tabs, cameras.
2. Real hardware: the "0.6.0 Studio" section of docs/HARDWARE_CHECKLIST.md (GPU backends incl. the GPU LUT pass, two
   webcams, a person in front of a camera for vision / face mesh / depth, privacy shield in Zoom / Teams, a real
   microphone with monitoring, the phone page over LAN).
3. Not possible in 0.6.0 and stated in the UI / docs: system virtual microphone (signed kernel driver,
   reports/VIRTUAL_AUDIO_DEVICE.md), SAM 2.1 video propagation (not in the ONNX export), identity documents / plates
   as objects (no such detector classes; their printing is found as text), active-speaker camera switching (one
   microphone gives no per-camera signal).
4. Partly: registered custom models of categories other than detection / YOLO-seg are checked, measured and kept,
   but no pipeline module loads them yet.

Everything else in docs/PRO_060_FEATURE_MATRIX.md is implemented, in the GUI, in the pipeline and in the tests
(full suite 783 passed, 0 failed, 40 skipped on 2026-11-20).

## Known limitations (also in the UI where they matter)

- No hardware GPU was available: every AI and colour number is CPU; DirectML on this server falls back to the CPU.
- Privacy shield: best effort (small / slanted / handwritten text missed; e-mail only via "all text"; extra cameras of
  a layout are not covered).
- Colour Studio runs on the CPU; with every effect on, the frame-rate guard drops sharpness / glow / grain / skin
  protection on slow PCs.
- Remote control over LAN uses plain HTTP.
- Multi-camera: at most 3 extra sources; no active-speaker switching.
