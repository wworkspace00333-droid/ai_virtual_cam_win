# 0.6.0 Professional — release readiness (2026-11-19)

**Verdict: not ready to publish as 0.6.0 final. Ready as a preview build (0.6.0-preview) for testing on real PCs**,
once the changes are committed and an installer is built. The code is complete for the scope in
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

## Open before a final 0.6.0

1. Done: committed (6633d2b) and tagged v0.6.0-preview with your permission; CI builds the preview installer.
2. Done in CI: the preview installer built (216 MB), installed silently on the runner and passed the installed-app
   check; it is attached to the draft pre-release v0.6.0-preview (not published). Still open: install it over
   0.5.3 on a PC that has 0.5.3 (an upgrade, not a fresh install) and open the Studio tabs there.
3. Real hardware: the "0.6.0 Studio" section of docs/HARDWARE_CHECKLIST.md (GPU backends, two webcams, a person in
   front of a camera for vision, privacy shield in Zoom / Teams, real microphone, phone over LAN).
4. Decide on the 16 MB text recognition model (needed for "only sensitive text"; until then all text is hidden).
5. Optional but announced in the prompt and not done: GPU LUT shader, depth effects, dense face mesh,
   active-speaker switching, virtual microphone (driver; see reports/VIRTUAL_AUDIO_DEVICE.md). Done after the
   preview build: adapter driver / feature levels, raised-hand event, pose-aware framing, microphone monitoring.

## Known limitations (also in the UI where they matter)

- No hardware GPU was available: every AI and colour number is CPU; DirectML on this server falls back to the CPU.
- Privacy shield: best effort (small / slanted / handwritten text missed; e-mail only via "all text"; extra cameras of
  a layout are not covered).
- Colour Studio runs on the CPU; with every effect on, the frame-rate guard drops sharpness / glow / grain / skin
  protection on slow PCs.
- Remote control over LAN uses plain HTTP.
- Multi-camera: at most 3 extra sources; no active-speaker switching.
