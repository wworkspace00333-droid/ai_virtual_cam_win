# Windows virtual microphone — feasibility (0.6.0 Phase 8)

Status: **not implemented, not shipped.** This report is the architecture study the master prompt asks for before
any driver work. The 0.6.0 Audio Studio processes the microphone for the app's own recordings only; it is not
presented as a system microphone anywhere in the UI.

## Why the camera approach does not carry over

The virtual camera uses the Windows 11 Media Foundation virtual camera API (`MFCreateVirtualCamera`) and a
DirectShow filter for older apps — both user-mode, no driver. Windows has **no user-mode equivalent for audio
capture endpoints**: an input device other apps (Zoom, Teams, browsers) can choose must be an audio endpoint created by
a kernel-mode audio miniport (PortCls / WaveRT) or by the newer ACX framework (Windows 11 22H2+, still kernel mode).

## What a real implementation needs

| Topic | Requirement |
|---|---|
| Driver model | WaveRT miniport on PortCls (Windows 10/11) or ACX (Windows 11 22H2+); a loopback design: the app writes processed PCM into the driver, the driver exposes it as a capture endpoint (the SYSVAD sample in the Windows-driver-samples repository is the usual starting point) |
| Signing | Kernel drivers load on 64-bit Windows only with an EV code-signing certificate **and** Microsoft attestation signing through the Hardware Dev Center (Partner Center account). Test-signing works only with `bcdedit /set testsigning on`, which lowers system security — the project rules forbid asking users to do that |
| Secure Boot / HVCI | The driver must pass HVCI (memory integrity) compatibility; otherwise it does not load on PCs with memory integrity on (default on new Windows 11 PCs) |
| Installation | INF + catalog, installed with `pnputil /add-driver /install` from an elevated installer; Inno Setup can run it, uninstall must remove the device node and the driver package (`pnputil /delete-driver /uninstall`) |
| Compatibility | Separate builds for x64 and ARM64; testing on Windows 10 22H2 and Windows 11; WHQL/HLK tests if distributed widely |
| Security | Kernel code runs with full privileges: a bug crashes the PC (bug check). Needs a minimal IOCTL surface, input validation, and fuzzing |
| Licensing | Windows-driver-samples are MIT-licensed; existing open drivers (e.g. VB-Audio's are freeware, not open source; Scream is MS-PL) cannot simply be bundled without checking terms |
| Cost / time | Certificate (~300–600 USD/year), Partner Center account, weeks of driver development and testing on real hardware |

## Alternatives considered

1. **Ask users to install an existing virtual audio cable** (for example VB-CABLE, freeware) and let the app play its
   processed audio into it. No driver work for the project; licensing allows personal use; the app would need an
   audio *output* path (WASAPI render), which 0.6.0 does not have yet. Cleanest next step.
2. **OBS route:** OBS already offers filters on microphone sources; users who stream through OBS can use them.
3. **Own driver** as above — only with a signing budget and a separate, opt-in installer component; never inside the
   main installer unsigned.

## Recommendation

Keep 0.6.0 as is (processing for recordings). For a later version: add a WASAPI render output so the processed
microphone can be played into a user-installed virtual cable (option 1), and document it. Revisit an own driver only
with an EV certificate and attestation signing in place.
