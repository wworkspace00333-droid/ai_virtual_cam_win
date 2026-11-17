# Third-party notices — AI Virtual Camera Studio for Windows

AI Virtual Camera Studio is proprietary software, free to install and use (see `LICENSE`). It includes the open-source components listed below,
each under its own licence. The full licence texts are installed with the program in
`<install folder>\_internal\licenses\`. The Windows installer is checked by `scripts/check_licenses.py`: no GPL or
AGPL component is allowed in it, except the separately licensed OBS plugin, which ships with its source (see the end).

Last reviewed: 2026-11-16, for version 0.4.1. This file is a technical record, not legal advice.

## Components that ship in the installer

| Component | Version | Licence | Where / how it is used |
|---|---|---|---|
| Qt for Python (PySide6, Shiboken6) and the Qt 6 libraries | 6.12.0 | LGPL-3.0-only (Qt's open-source option; GPL-only Qt modules are excluded from the build) | user interface; shipped as separate, unmodified DLLs that can be replaced |
| FFmpeg (libavcodec, libavformat, libavutil, libswscale, libswresample, libavfilter, libavdevice) | n9.0.2 (BtbN FFmpeg-Builds `lgpl-shared`, 2026-11-15) | LGPL-3.0-or-later; configured with `--enable-version3`, without `--enable-gpl`, without libx264 / libx265 | video decoding of replacement clips, through PyAV; separate, unmodified DLLs |
| PyAV | 19.0.2 (built from source against the FFmpeg above, `scripts/build_pyav_lgpl_win.ps1`) | BSD-3-Clause | video decoding |
| ONNX Runtime (DirectML build) | 1.24.4 | MIT; bundled third-party notices in `licenses\onnxruntime\ThirdPartyNotices.txt` | AI inference |
| DirectML (`DirectML.dll`, part of the ONNX Runtime DirectML package) | as shipped with ONNX Runtime 1.24.4 | Microsoft licence permitting redistribution with applications (see ONNX Runtime's notices) | AI on DirectX 12 GPUs |
| OpenCV (opencv-python-headless) | 4.14.0 | Apache-2.0; the wheel bundles FFmpeg (LGPL) and other libraries listed in `licenses\opencv-python-headless\LICENSE-3RD-PARTY.txt` | image processing, DirectShow capture, YuNet face detector |
| NumPy | 2.5.4 | BSD-3-Clause (and 0BSD, MIT, Zlib, CC0-1.0 parts) | arrays |
| SciPy | 1.18.1 | BSD-3-Clause | tracking (assignment) |
| Pillow | 13.0.0 | MIT-CMU (HPND) | image formats |
| psutil | 7.2.2 | BSD-3-Clause | CPU / memory statistics |
| protobuf, flatbuffers, sympy, mpmath, packaging | 7.36.2, 25.12.19, 1.14.0, 1.3.0, 26.3 | BSD-3-Clause, Apache-2.0, BSD-3-Clause, BSD-3-Clause, Apache-2.0 OR BSD-2-Clause | dependencies of ONNX Runtime |
| DirectShow BaseClasses (Microsoft Windows-classic-samples) | as in `native/win/third_party/baseclasses` | MIT | built into `vcam_dshow.dll` (the DirectShow camera) |
| Python runtime | 3.12 | PSF License | the program's interpreter (PyInstaller bundle) |
| PyInstaller bootloader | 6.x | GPL-2.0 with the PyInstaller bootloader exception (allows distribution with any program) | starts `vcstudio.exe` |

## Models and sample data

| Item | Licence | Source | SHA-256 |
|---|---|---|---|
| YOLOX-Nano, YOLOX-Tiny, YOLOX-S (object detection, ONNX) | Apache-2.0 (code and weights) | Megvii-BaseDetection/YOLOX, release 0.1.1rc0 | `c789161e…`, `427cc366…`, `c5c2d13e…` (full values in `scripts/fetch_models.py`) |
| YuNet face detector `face_detection_yunet_2023mar.onnx` | MIT, © 2020 Shiqi Yu | OpenCV Zoo | `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4` |
| Person segmentation PP-HumanSeg `human_segmentation_pphumanseg_2023mar.onnx` (background blur / replacement) | Apache-2.0, © 2021 PaddlePaddle Authors (`licenses\PP-HumanSeg-LICENSE.txt`) | PaddleHub, ported to ONNX by OpenCV Zoo | `552d8a984054e59b5d773d24b9b12022b22046ceb2bbc4c9aaeaceb36a9ddf24` |
| Sample photo `street.jpg` | CC0 1.0 (public domain dedication) | Wikimedia Commons, "Moscow, Paveletskaya Square, people at bus stop Sept 2025 02.jpg" by Retired electrician, 1280 px rendition | `8b70107df5dff1c92309dca9db880d6245f68ec71b5bd6883102efaeadf80a58` |
| Sample logo and clips (`logo_alpha.png`, `clip_h264.mp4`) | made for this project | — | — |

The face detector finds face rectangles only; the program does no identity recognition.

Optional models for "any object". They are not in the installer; the app downloads them from huggingface.co only
when the user clicks "Download models" on the Detection page, and checks each file's SHA-256 (values in
`vcstudio/ai/model_download.py`). Besides "Check for updates" (one request to the GitHub releases list, on click),
this is the program's only network access. `VCS_NO_NETWORK=1` or the setting `privacy.allow_downloads: false` turns
it off. Developers can use `scripts/fetch_models.py --open-vocab` instead.

| Item | Licence | Source |
|---|---|---|
| OWLv2 base patch16 ensemble, 8-bit ONNX (`owlv2_base_ensemble_int8.onnx`, `owlv2_tokenizer.json`) | Apache-2.0 (Google, `google/owlv2-base-patch16-ensemble`) | ONNX export `Xenova/owlv2-base-patch16-ensemble`, commit `3e255bc9` |
| SAM 2.1 hiera-tiny, 8-bit ONNX (`sam2.1_tiny/`) | Apache-2.0 (Meta, `facebook/sam2.1-hiera-tiny`) | ONNX export `onnx-community/sam2.1-hiera-tiny-ONNX`, commit `814a0666` |

SHA-256 of every file: `scripts/fetch_models.py`. The CLIP text tokenizer is implemented in the program
(`vcstudio/ai/open_vocab.py`, from the model's `tokenizer.json`); no tokenizer library is shipped.

## Not included (licence reasons)

- **Ultralytics YOLO11 models** (AGPL-3.0): no longer in the installer. A user can add them with "Import model" on their
  own responsibility; the program does not download them.
- **x264 / x265** (GPL-2.0+): the FFmpeg build above is compiled without them; the program only decodes video.
- **COCO test photos and Ultralytics' `bus.jpg`**: used only by the developers' test suite, not shipped.
- **GPL-only Qt modules** (Qt Charts, Data Visualization, Graphs, Quick 3D, Virtual Keyboard, Lottie, ...): excluded
  from the build (`packaging/vcstudio_win.spec`).

## Source code of LGPL components

Qt and FFmpeg are used under the LGPL as separate, unmodified, dynamically linked libraries; you may replace them with
your own builds of compatible versions. Their complete corresponding source code is available from:
- Qt 6.12.0 / PySide6 6.12.0: https://download.qt.io/official_releases/ and https://code.qt.io/
- FFmpeg n9.0.2, commit `d838f9f9ae`: https://git.ffmpeg.org/ffmpeg.git; build scripts: https://github.com/BtbN/FFmpeg-Builds

On request we also provide a copy of this source code, for at least three years after we last distributed this
version, for no more than the cost of physically performing the distribution.

## Patents

Decoding H.264 / HEVC and other formats may be covered by patents in some countries. Software licences do not grant
patent licences. The program decodes such formats only for the user's own replacement clips.

## OBS plugin (open source, GPL-2.0-or-later)

`vcstudio-obs.dll`, installed into OBS Studio as an optional plugin, links against OBS Studio (`obs.dll`,
`obs-frontend-api.dll`, GPL-2.0-or-later) and is therefore itself licensed under the **GNU General Public License,
version 2 or (at your option) any later version**. It is a separate work from AI Virtual Camera Studio: the program
does not link the plugin or OBS; the two only exchange frames through shared memory.

Its complete source code (`vcstudio-obs.c`, the protocol header `vcs_frame_shm.h` — MIT licence — and the build
scripts) and the GPL text (`COPYING`) are installed with every copy of the plugin, in `<install folder>\obs-plugin-source\`
and `<install folder>\_internal\obs-plugin\source\`. It is built with MSVC from these files and the OBS Studio headers
of the matching version (https://github.com/obsproject/obs-studio). You may modify and redistribute the plugin under
the GPL.
