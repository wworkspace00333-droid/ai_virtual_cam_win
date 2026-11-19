# 0.6.0 Professional — test results (2026-11-19)

Machine: development server (Windows Server 2025, 2 cores, no GPU, no webcam, no microphone). Commands as in
docs/TESTING.md; nothing was skipped on purpose to make numbers look better.

## Totals

| Run | Passed | Failed | Skipped | Time |
|---|---|---|---|---|
| Full suite `pytest tests` (offscreen) | **760** | **0** | 40 | 5 min 15 s |
| Live + stress (`VCS_LIVE=1 VCS_STRESS=1`, camera / capture / integration / packaging / pipeline / vcam / stress files) | **71** | **0** | 16 | 3 min 34 s |
| Soak, all Studio features at once, 10 min (scripts/pro060_soak.py) | pass | | | 29.9 fps avg, +7.2 MB / 10 min, 0 errors |
| After the preview build (driver / feature levels, raised hand, pose framing, monitoring): full suite | **768** | **0** | 40 | 5 min 16 s |
| ruff | clean | | | |
| mypy (CI scope, 53 files incl. the new core modules) | clean | | | |

Before 0.6.0 work (0.5.3): 630 passed, 0 failed, 44 skipped. The first full run of 0.6.0 had 4 failures, all
expectations that the new features changed on purpose (14 pages instead of 13; the local remote server as a new
network user; the "vision" scheduler module); the tests were updated with the reason in their docstrings.

Skips: tests that need a drawn desktop for screen capture (RDP session without a visible desktop at run time), macOS
paths, a hardware GPU or optional models; the same set as in 0.5.3 minus four that now ran.

## New tests (125) by area and kind

| File | Tests | Unit | Integration (AppContext / pipeline) | GUI | Real data |
|---|---|---|---|---|---|
| test_model_check.py | 24 | rules for 9 categories | backend check | — | 7 real models, wrong category, broken file, tamper |
| test_multicam.py | 13 | layouts, compose pixels, stale policy, auto switch | second live source in the pipeline, loop risk | — | bundled video as 2nd source |
| test_vision.py | 15 | head-pose round trip / directions | overlay in the pipeline, service, stale rejection | — | YuNet + pose on bus.jpg |
| test_background_studio.py | 8 | fills, guided filter edge, tracker lock / re-acquire | video background through the app | — | bundled clip |
| test_privacy_shield.py | 19 | patterns (incl. false positives), hold, strict, effects | shield in the pipeline, passthrough | — | real text detector: 5/5 lines found, 0 boxes on a smooth picture, tiny text documented as missed |
| test_color_studio.py | 9 | tone direction, saturation, skin, .cube parse, LUTs, effects, frame-rate guard | graded exactly once, follows the scene | — | — |
| test_automation.py | 8 | debounce, cooldown, priority, dedup, loop protection, bounded queue | engine thread + app actions | — | — |
| test_audio_fx.py | 11 | gain, limiter, compressor ratio, gate, EQ, denoise (−16 dB noise), AGC, block-size independence | chain inside the recording service | — | synthetic signals |
| test_remote.py | 7 | token store (DPAPI), limiter, WS frames | real HTTP + WebSocket: auth, whitelist, host / origin, size, lockout, renew | — | real sockets |
| test_ui_studio.py | 11 | — | — | every Studio tab drives its service; background card | — |

Failure tests included: broken model file, unfit model registration, file changed after check, missing LUT, bad LUT,
action that raises, queue overflow, detector error in strict mode, missing models for vision, own camera as extra
source, oversized / malformed / cross-site API requests.

## Performance tests

See [PRO_060_PERFORMANCE.md](PRO_060_PERFORMANCE.md): 21 pipeline runs (7 scenarios × 720p30 / 1080p30 / 1080p60),
all within 2 % of the input rate after the colour frame-rate guard; component timings.

## What these tests do not show

All video input was synthetic (test pattern, bundled clips and photos); no hardware GPU, webcam, microphone or
phone was available. Accuracy of pose / head pose / text detection on live video was not measured beyond the photos
named above. See the release readiness report.
