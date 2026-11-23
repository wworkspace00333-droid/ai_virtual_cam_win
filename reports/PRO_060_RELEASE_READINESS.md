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
| 8 | Installer updates safely | ✅ | fresh install in CI and upgrade 0.5.2 → 0.6.0-preview2 on this server (2026-11-20): exit 0, cameras registered, self-check 13/13, e2e ok, data kept |
| 9 | Uninstall leaves nothing | ⬜ for 0.6.0 | no new system components were added (no driver, no service, no firewall rule); new data files live in app data |
| 10 | Model and dependency licences checked | ✅ | 3 new Apache-2.0 models in THIRD_PARTY_NOTICES; no new Python dependencies |
| 11 | New APIs protected | ✅ | reports/PRO_060_SECURITY.md, tests/test_remote.py |
| 12 | Documentation current | ✅ | README, CHANGELOG (Unreleased), ARCHITECTURE, HARDWARE_CHECKLIST, PRO_060_* docs and reports |

## Open before a final 0.6.0 (state 2026-11-20, after 0.6.0-preview2)

Published pre-releases: v0.6.0-preview and **v0.6.0-preview2** (installer 227 MB + .sha256; CI build, silent install
and installed-app check passed). 0.5.3 stays the latest release, so 0.5.x users are not offered the previews.

1. Done 2026-11-20 — upgrade over an installed version on this server: after the owner rebooted it (the pending
   replace from the earlier update completed), the downloaded 0.6.0-preview2 installer (SHA-256 checked) upgraded
   0.5.2 silently: exit code 0 in 26 s, version 0.6.0-preview2, both cameras registered again; vcam_dshow.dll was in
   use by another process and is replaced at the next restart (the previous DLL keeps the camera working meanwhile).
   Installed app: self-check 13/13; camera loopback ok (60 fps capture, median latency 11 ms, delivery 70 %);
   pipeline end-to-end ok (61 fps, latency 12 / 14 ms p50 / p95, 0 render errors, 0 dropped, client 60 fps);
   settings, profile and data folder kept.
2. Real hardware: the "0.6.0 Studio" section of docs/HARDWARE_CHECKLIST.md (GPU backends incl. the GPU LUT pass, two
   webcams, a person in front of a camera for vision / face mesh / depth, privacy shield in Zoom / Teams, a real
   microphone with monitoring, the phone page over LAN).
3. Not possible in 0.6.0 and stated in the UI / docs: system virtual microphone (signed kernel driver,
   reports/VIRTUAL_AUDIO_DEVICE.md), SAM 2.1 video propagation (not in the ONNX export).
   Added 2026-11-23 as best effort (🟡, measured on synthetic input only): identity documents / cards / plates as
   whole objects (outline + text inside, ai/document_detect.py) and active-speaker switching by mouth movement
   (ai/speaker.py); both are in the hardware checklist.
4. Partly: registered custom models can be used for background segmentation, text detection, pose landmarks,
   hand landmarks, face detection and face mesh (the layouts of the built-in models); semantic-segmentation layouts
   are checked and kept only.
5. Done 2026-11-23 — the OBS plugin with the new access list compiles: CI build of v0.6.0-preview3 (tests and
   installer jobs passed). Loading it in a real OBS is part of the hardware checklist.
6. Done 2026-11-23 — audio/video sync of recordings with noise suppression: constant 21 ms delay, compensated in the
   recorder; tested with a recorded tone + flash (within 10 ms).

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
