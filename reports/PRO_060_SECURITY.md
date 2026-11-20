# 0.6.0 Professional — security review

Scope: everything new in 0.6.0 (remote control, model import, LUT import, privacy shield, automation, multi-camera,
downloads). Method: code review of each entry point plus the tests listed; no destructive or external testing.

## New attack surface and mitigations

| Surface | Risk | Mitigation (code) | Test |
|---|---|---|---|
| Remote control HTTP / WebSocket (app/remote.py) | control of the camera from another program or device | **off by default**; binds 127.0.0.1; LAN only by explicit opt-in with a warning | test_remote: off by default, bound address |
| | stolen / guessed access code | 256-bit random token, constant-time compare, 5 failures/min → 60 s lockout, "New code" revokes | lockout, renew tests |
| | token at rest | Windows DPAPI (CryptProtectData, current user); never written in clear text on Windows | token store test checks the file bytes |
| | DNS rebinding / cross-site requests from a web page | Host header must be localhost (local mode); POST with a foreign Origin refused; WebSocket checks Origin | 421 / 403 tests |
| | command injection | fixed whitelist of 7 action types with typed fields (strings ≤ 64 chars); scenes only by existing name; no shell, no eval, no paths, no file serving except the built-in page | whitelist / bad-value tests |
| | resource exhaustion | per-address token bucket (10/s, burst 30); body ≤ 4 KB; WebSocket frames ≤ 4 KB, masked frames only | 413 / 429 tests |
| | headers | no-store, nosniff, X-Frame-Options DENY, no-referrer, CSP on the page; server version hidden | — |
| | thread safety | API and automation decide off the UI thread; actions run on the Qt thread via AppContext.call_main | app tests |
| Model check / registry (ai/model_check.py) | malicious model file | ONNX Runtime loads built-in operators only (no custom-op libraries registered); files hashed and copied, never executed; 2 GB limit; full SHA-256 re-check (`verify`) before use; copy re-hashed (TOCTOU) | tamper and changed-file tests |
| Model downloads (pose, text) | supply chain | fixed https://huggingface.co URLs pinned to commits; SHA-256 checked before the file gets its final name; only on the user's click; VCS_NO_NETWORK / settings switch off | existing download tests; hashes verified at download |
| LUT import (replacement/color.py) | malformed / huge file | full parse before copying, 30 MB limit, LUT size 2..129, domain check; copied into the app folder under a sanitised name | parse / import error tests |
| Privacy shield | false sense of safety | UI states the limits (small / slanted / handwritten text, e-mail needs "all text", extra cameras not covered); strict mode holds areas, shows the shield even in passthrough, darkens the frame if the shield itself fails | strict / failure tests |
| OBS frame section (native/obs-plugin) | another process in the session reads or writes the picture | explicit access list owner / SYSTEM / administrators (VCS_SHM_SDDL, 2026-11-20); the reader also refuses sections owned by others (0.5.3) | test_obs_section_access_list…, owner test |
| Multi-camera sources | feedback loop through the own camera | this app's camera (any name) and the configured output device are refused as extra sources | loop-risk test |
| Automation | runaway scene switching | cooldowns, debounce, priority, dedup, bounded queue, loop protection (4 switches / 20 s → 30 s pause) | loop protection test |
| Audio | none new outward | processing is local; no virtual device; microphone opens only while recording / replay / live meters are on | — |

## Residual risks (accepted, documented)

- LAN mode uses plain HTTP: on an untrusted network the access code can be sniffed. The UI warns; HTTPS would need a
  certificate the phone trusts — not in 0.6.0.
- Anyone logged in as the same Windows user can read the DPAPI-protected token (by design of DPAPI CurrentUser).
- The privacy shield is best-effort detection; it is not a redaction guarantee.

## Not changed

No Windows security settings, Defender, UAC or firewall rules are touched by the app. During development one GUI
test briefly bound the LAN address, which can make Windows Firewall show an "allow access" prompt on an interactive
desktop; the test was changed so that it no longer opens a network socket.
