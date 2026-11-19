# Hardware report and hardware compatibility assessment — 2026-11-19

## 1. This computer (measured)

| Item | Value | Source |
|---|---|---|
| Windows | Windows Server 2025 Datacenter 24H2, build 26100, x64 | `windows_pc_report.py`, `Win32_OperatingSystem` |
| CPU | Intel Xeon 6975P-C, **2 physical cores / 4 logical** | `Win32_Processor` |
| RAM | 31.4 GB total, 24.3 GB free at the time of the audit | `Win32_OperatingSystem` |
| GPUs | Microsoft Basic Display Adapter (driver 10.0.26100.1, AdapterRAM 0), Microsoft Remote Display Adapter (RDP) — **no hardware GPU** | `Win32_VideoController`; app: `hardware GPU: False` |
| Direct3D 11 | device created on **Microsoft WARP (software)**, feature level 11_0 available | `D3D11Context()` → `software_warp: true` |
| DirectX runtime | 4.09.00.0904 (registry); DirectX 12 runtime present (Windows 11/Server 2025) | registry |
| ONNX Runtime | 1.24.4 (`onnxruntime-directml`), providers reported: `DmlExecutionProvider`, `CPUExecutionProvider` | `ort.get_available_providers()` |
| CUDA | not present (no NVIDIA GPU, CUDA EP not in the package) | — |
| Disk C: | 60 GB; 1.5–2.4 GB free during the audit, **12 GB free** after removing leftover test/soak temp folders (see FULL_PROJECT_AUDIT §8) | `df`, `Get-PSDrive` |
| Python | 3.12.10 (venv `C:\vcs-env`); OpenCV 4.14.0, NumPy 2.5.4, PySide6 6.12.0, PyAV 19.0.2 | import check |
| Media Foundation | Frame Server service present; the app's MF camera registered and readable (720p/1080p, NV12/RGB32, 30/60 fps measured) | `windows_pc_report.py` |
| DirectShow | the app's DirectShow camera registered; readable by the DirectShow client tests | `test_vcam_win.py` |
| Physical cameras | none | — |

### 1.1 Real provider check (not just "installed")

Script `ep_check.py` (audit scratch): a session per provider for two shipped models, 20 inferences each, then
`session.get_providers()`.

| Model | Requested provider | Provider actually used | p50 ms | p95 ms |
|---|---|---|---|---|
| yolox_nano.onnx | DmlExecutionProvider (device 0) | **CPUExecutionProvider** | 11.6 | 15.0 |
| yolox_nano.onnx | CPUExecutionProvider | CPUExecutionProvider | 11.5 | 11.7 |
| pphumanseg (192×192) | DmlExecutionProvider (device 0) | **CPUExecutionProvider** | 6.0 | 6.6 |
| pphumanseg | CPUExecutionProvider | CPUExecutionProvider | 6.0 | 6.2 |
| yolox_nano via the app (`create_session(..., "all")`) | app's plan | **CPU** (app label "CPU") | — | — |

DirectML is installed and listed, but on this machine it cannot run (no hardware adapter): ONNX Runtime silently
puts the graph on the CPU. The app does not request DirectML at all here (`_best_dml_device()` finds no hardware
GPU) and labels the backend "CPU", so the UI does not claim GPU acceleration. **CPU fallback works and is reported
correctly; the DirectML path itself is not verified on this machine.** (These raw-session numbers use all cores; the
app limits inference to half the physical cores, so its numbers are higher — see §2.)

## 2. AI and pipeline numbers on this machine (CPU only)

From `windows_pc_report.py --quick` and the audit's module script (sample photo 1280×863, app thread settings):

| Model | p50 ms | p95 ms | Notes |
|---|---|---|---|
| yolox_nano (shipped) | 15.8 – 23.7 | 15.9 – 24.9 | lower value: report run; higher: audit run with the soak-free machine but other modules loaded |
| yolox_tiny (shipped) | 54.3 | 55.9 | |
| yolox_s (shipped) | 189–195 | 193–209 | the "auto" choice starts here in budget modes until the step-down |
| YuNet faces (shipped) | 23.2 | 23.8 | OpenCV DNN, CPU |
| PP-HumanSeg (shipped) | 17.4 | 17.7 | raw session 6 ms; the rest is the 1-thread limit and pre/post-processing |
| OWLv2 (download) | 7616 | 7913 | ~0.1 fps; process memory grew by ~2.8 GB during the measurement |
| SAM 2.1 tiny (download) per frame, 4 boxes | 3521 | 3714 | |

Camera formats through Frame Server (MF camera): 1080p NV12 30 → 30.0 fps, 60 → 59.8; 720p NV12 30 → 29.9, 60 → 59.8;
1080p/720p RGB32 30 → 30.0/29.9.

## 3. How the app adapts to hardware today (source review)

| Concern | What exists | Where | Verified here? |
|---|---|---|---|
| Provider choice | CUDA (if present) → DirectML on the hardware adapter with most dedicated VRAM → CPU; label shows the provider actually used | `ai/onnx_engine.py` `provider_plan`, `_best_dml_device`, `create_session` | CPU path yes; GPU paths no |
| Optimised-graph cache per provider/GPU/ORT version; broken cache removed | `_cache_file` | yes (tests, D23) |
| CPU share for AI | inference threads = half the physical cores (1–8) so capture/output keep CPU | `cpu_threads` | yes (output holds 60 fps while AI runs) |
| Model choice | first run measures the detector and picks the speed mode; budget modes step down from the largest model until within 20–25 ms | `ai/auto_profile.py`, `engine._max_quality_choice` | yes (step-down seen in benchmark logs) |
| AI rate | adaptive rate + resource scheduler: background segmentation slowed first, then detector; AI on demand (2 fps idle) | `core/scheduler.py`, `ai/adaptive.py` | yes |
| Memory | warning/critical thresholds from installed RAM (auto) or manual; critical → Low Resource mode, close players, drop caches | `core/performance.py`, `core/watchdog.py` | logic by tests; real low-RAM machine not tested |
| Compositor | Direct3D 11 (GPU or WARP) → CPU fallback on failure; device-lost recreation | `platform/win/d3d11.py`, `replacement/compositor.py` | WARP + CPU yes; real GPU and device loss on hardware no |
| VRAM | no VRAM budget or measurement (`dedicated_vram_mb` only used to pick the adapter) | — | not applicable here |
| Open vocabulary / SAM on weak machines | allowed on any machine; no admission check for RAM (≈3–4 GB growth measured) | `ai/open_vocab.py` | measured: very slow on CPU |

## 4. Readiness per hardware class (assessment)

| Class | Expected behaviour from code + measurements | Confidence |
|---|---|---|
| CPU-only, 2 cores (this server) | video 1080p60 holds (57 fps avg in the matrix, 60 median in soak; p05 42 under the heaviest load); AI 12–20 fps with yolox_nano; background blur/replace ok; open vocabulary impractical | **measured** |
| CPU-only, 4–8 cores | more AI threads (half the cores), higher AI rates; same code path | inferred, not measured |
| NVIDIA GPU | DirectML on the GPU (no CUDA EP in the shipped package); D3D11 on the GPU | **not verified** |
| AMD / Intel GPU | DirectML + D3D11 on the GPU; Intel iGPU shares RAM | **not verified** |
| Low RAM (8 GB) | base footprint ~550–770 MB; thresholds scale; OWLv2+SAM (≈4 GB) could exhaust memory | inferred; risk for open vocabulary |
| Low VRAM | no VRAM accounting; relies on reload after repeated errors and model fallback | **not verified** |

## 5. Is a separate Hardware Compatibility Layer needed?

Most of its functions already exist but are spread over `onnx_engine`, `auto_profile`, `scheduler`, `watchdog` and
`performance`. What is missing is one place that **knows the machine** and that the others consult:

1. a VRAM/RAM budget (today only RAM thresholds for the whole process);
2. admission control for heavy modules (OWLv2/SAM refuse or warn when RAM/VRAM is insufficient);
3. a hardware profile saved with the first-run measurement (adapter, driver, VRAM, cores, RAM, measured ms per
   model per provider) so later choices are not re-derived;
4. AI input resolution as a lever (today the model input is fixed per model);
5. a verified "DirectML actually used" check after session creation on a GPU machine (compare `get_providers()`
   and timing against CPU).

**Recommendation:** not a rewrite. Add a small `core/hardware.py` (`HardwareProfile` dataclass + `probe()` +
`budget()`), filled once at first run and on driver/adapter change, and make `provider_plan`, `auto_profile`,
`scheduler` and the open-vocabulary loader read it. Steps, each testable on its own:

| Step | Change | Files | Done when | Test |
|---|---|---|---|---|
| 1 | `HardwareProfile` from existing `sysinfo` (adapters, VRAM, RAM, cores, driver) saved in settings | new `core/hardware.py`, `platform/win/sysinfo.py` | profile in diagnostics ZIP | unit test with fake adapters |
| 2 | Post-load provider verification (DirectML requested → `get_providers()[0]` must be DML, else mark "GPU unavailable") | `ai/onnx_engine.py` | wrong label impossible | unit test with a fake session; real check on a GPU PC |
| 3 | Admission control for OWLv2/SAM (RAM free and VRAM) with a clear UI message | `ai/open_vocab.py`, `ui/pages/detection_page.py` | no OOM on 8 GB PCs | test with a fake profile |
| 4 | Budget-aware model/mode choice using the stored per-provider timings | `ai/auto_profile.py`, `ai/engine.py` | no re-measurement each start | unit tests |
| 5 | VRAM monitoring (DXGI QueryVideoMemoryInfo) feeding the watchdog | `platform/win/gpu_monitor.py`, `core/watchdog.py` | VRAM pressure sheds load | only on a GPU PC |

Steps 1–4 can be built and unit-tested on this server; step 5 and every GPU claim need real hardware (P0.1).
