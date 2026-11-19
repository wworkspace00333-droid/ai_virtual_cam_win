# 0.6.0 Professional — performance

Machine: development server, Windows Server 2025, Intel Xeon 2 cores / 4 threads, 31.4 GB RAM, **no hardware GPU**
(compositor on Direct3D 11 WARP, AI on the CPU; DirectML listed but falls back to the CPU — measured by
Studio → AI Models → Check backends). Input: **synthetic** test pattern (no webcam on this server). Camera output off
(the virtual-camera write path is unchanged since 0.5.x and measured there). Not measured: GPU utilisation and VRAM
(no GPU).

## Pipeline: before (baseline) and with each Studio feature

`scripts/pro060_bench.py --seconds 8` (raw data: [pro060_bench.json](pro060_bench.json)). AI detection off in all rows,
so each row shows only the feature's own cost. CPU is the process total in % of one core (400 % = all 4 threads).

| Scenario | Size | Output FPS | Latency p50 ms | p95 ms | Processing ms | CPU % | RSS MB | Inputs skipped |
|---|---|---|---|---|---|---|---|---|
| baseline | 1280x720@30 | 30.0 | 0.2 | 0.2 | 0.1 | 4 | 137 | 0 |
| multicam PiP (1 extra source) | 1280x720@30 | 29.9 | 4.5 | 5.0 | 4.5 | 6 | 160 | 0 |
| colour: tone + saturation | 1280x720@30 | 30.1 | 4.9 | 6.4 | 4.9 | 36 | 158 | 0 |
| colour: everything | 1280x720@30 | 30.0 | 16.8 | 17.4 | 17.0 | 118 | 171 | 1 |
| background gradient (Balanced) | 1280x720@30 | 30.0 | 4.0 | 7.2 | 5.1 | 58 | 221 | 0 |
| vision (face, 6/s, overlay) | 1280x720@30 | 29.9 | 0.2 | 0.2 | 0.1 | 36 | 222 | 0 |
| privacy shield (all text, 2/s) | 1280x720@30 | 30.0 | 0.2 | 0.3 | 0.1 | 50 | 241 | 0 |
| baseline | 1920x1080@30 | 30.0 | 0.3 | 0.4 | 0.3 | 6 | 191 | 0 |
| multicam PiP | 1920x1080@30 | 30.0 | 9.7 | 10.3 | 9.6 | 61 | 242 | 0 |
| colour: tone + saturation | 1920x1080@30 | 29.9 | 10.5 | 10.9 | 10.5 | 79 | 232 | 0 |
| colour: everything | 1920x1080@30 | 30.0 | 14.6 | 18.4 | 14.7 | 151 | 286 | 4 |
| background gradient | 1920x1080@30 | 29.9 | 9.1 | 11.7 | 9.5 | 127 | 316 | 0 |
| vision (face) | 1920x1080@30 | 30.0 | 0.3 | 0.4 | 0.2 | 42 | 306 | 0 |
| privacy shield | 1920x1080@30 | 30.1 | 0.3 | 0.6 | 0.3 | 50 | 347 | 0 |
| baseline | 1920x1080@60 | 59.8 | 0.3 | 0.3 | 0.3 | 17 | 281 | 0 |
| multicam PiP | 1920x1080@60 | 59.5 | 9.5 | 19.5 | 10.2 | 130 | 325 | 2 |
| colour: tone + saturation | 1920x1080@60 | 60.0 | 10.8 | 13.3 | 10.7 | 147 | 313 | 0 |
| colour: everything | 1920x1080@60 | 58.6 | 15.9 | 25.4 | 16.8 | 267 | 349 | 21 |
| background gradient | 1920x1080@60 | 59.3 | 8.6 | 22.8 | 8.3 | 198 | 317 | 5 |
| vision (face) | 1920x1080@60 | 59.8 | 0.3 | 0.4 | 0.2 | 40 | 307 | 0 |
| privacy shield | 1920x1080@60 | 59.9 | 0.3 | 0.5 | 0.3 | 51 | 347 | 0 |

Before the frame-rate guard of the colour stage (first run of the same script), "colour: everything" held 1080p
at **26 fps** (38 ms per frame); with the guard (expensive effects skipped while over half a frame interval) it
keeps 30.0 / 58.6 fps. Vision and the privacy shield run on their own threads: the output thread only draws their
newest results, so their cost shows as CPU, not as latency.

## Component measurements (same machine)

| Component | Measurement |
|---|---|
| Multi-camera composition 1080p: PiP with 3 windows / side by side / 2×2 | 13.0 / 6.6 / 8.2 ms p50 (first version: PiP 31 ms; fixed: uint8 blends, two-step resize, no crop copies) |
| Vision at 1080p (CPU): face + head pose / body pose (1 person) / hands | 20.9 / 36.1 / 17.1 ms p50; all three ≈ 74 ms per analysed frame |
| Text detection (PP-OCRv3, 736 px) | ≈ 110 ms per run, run 2×/s on its own thread |
| Colour stage 1080p: tone / saturation / skin / 3D LUT / vignette+grain+glow | 4.2 / 6.2 / 17.8 / 18.4 / 18.5 ms (720p: 2.1 / 3.1 / 10.0 / 11.9 / 8.5) |
| Audio chain, everything on | 10 s of 48 kHz stereo in 140 ms (1.4 % of one core) |
| Model check, e.g. YuNet / pose / YOLO11n-seg on CPU | 4.2 / 7.6 / 51 ms median per inference |

## Not measured here

Hardware GPUs (DirectML, D3D11 compositor on a real GPU, VRAM), physical webcams (capture latency, two cameras at
once), the virtual camera in Zoom / Teams with the new features, long soak runs with the new features (the 60-minute
soak of 0.5.2 covers the unchanged base pipeline). See docs/HARDWARE_CHECKLIST.md.
