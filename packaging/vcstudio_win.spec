# PyInstaller spec: AI Virtual Camera Studio for Windows (built by scripts/build_installer_win.ps1).
# One-folder build: dist\AI Virtual Camera Studio\vcstudio.exe + _internal\ (sys._MEIPASS):
#   native\      vcs_d3d11.dll, vcs_mfcap.dll, vcs_wgc.dll, vcs_dupl.dll (screen capture), vcam_dshow.dll, vcam_mf.dll, vcam-setup.exe, mf_client_test.exe
#   models\      YOLOX detectors (Apache-2.0) + YuNet faces (MIT); only the models listed in SHIPPED_MODELS:
#                Ultralytics YOLO (AGPL-3.0) in a developer's models\ folder is never bundled
#   obs-plugin\  vcstudio-obs.dll
#   sample_data\ public-domain sample photo, own logo and clip (no COCO photos: mixed licences)
#   LICENSE.txt, THIRD_PARTY_NOTICES.md
#   scripts\     developer tools (--run-script)
# The installer (packaging/windows/installer.iss) puts the folder in Program Files and registers the camera
# from there with native\vcam-setup.exe.
import glob
import os
from pathlib import Path

ROOT = Path(SPECPATH).parent
VERSION = os.environ.get("VCS_VERSION", "0.5.1")
NATIVE = ROOT / "build" / "native"

SHIPPED_MODELS = ["yolox_nano", "yolox_tiny", "yolox_s", "face_detection_yunet_2023mar",
                  "human_segmentation_pphumanseg_2023mar"]   # see scripts/fetch_models.py
datas = []
for stem in SHIPPED_MODELS:
    mp = ROOT / "models" / f"{stem}.onnx"
    if not mp.exists():
        raise SystemExit(f"missing model {mp}: run scripts/fetch_models.py")
    datas.append((str(mp), "models"))
    if mp.with_suffix(".json").exists():
        datas.append((str(mp.with_suffix(".json")), "models"))
for name in ("street.jpg", "logo_alpha.png", "clip_h264.mp4"):
    datas.append((str(ROOT / "tests" / "data" / name), "sample_data"))
for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
    datas.append((str(ROOT / name), "."))
datas.append((str(ROOT / "packaging" / "AppIcon.ico"), "."))            # window and tray icon
# licence texts of everything that ships: packaging/licenses (GPL/LGPL texts, YOLOX, YuNet), DirectShow BaseClasses,
# and the licence files of every runtime Python package (their dist-info), ONNX Runtime's notices, PyAV's FFmpeg.
for f in (ROOT / "packaging" / "licenses").glob("*.txt"):
    datas.append((str(f), "licenses"))
datas.append((str(ROOT / "native" / "win" / "third_party" / "baseclasses" / "LICENSE"), "licenses/directshow-baseclasses"))
import importlib.metadata as _md  # noqa: E402
import sys as _sys  # noqa: E402
_sys.path.insert(0, str(ROOT / "scripts"))
from check_licenses import closure  # noqa: E402
for _k, _d in closure().items():
    for _f in _d.files or []:
        _s = str(_f)
        if ".dist-info" in _s and any(x in Path(_s).name.upper() for x in ("LICENSE", "COPYING", "NOTICE")):
            datas.append((str(_d.locate_file(_f)), f"licenses/{_k}"))
_ort = Path(_md.distribution("onnxruntime-directml").locate_file("onnxruntime"))
for _n in ("LICENSE", "ThirdPartyNotices.txt"):
    if (_ort / _n).exists():
        datas.append((str(_ort / _n), "licenses/onnxruntime"))
_avd = Path(_md.distribution("av").locate_file("av"))
if (_avd / "FFMPEG_LICENSE.txt").exists():
    datas.append((str(_avd / "FFMPEG_LICENSE.txt"), "licenses/ffmpeg"))
datas.append((str(ROOT / "vcstudio" / "camera" / "camera_test.html"), "vcstudio/camera"))   # Virtual Camera page
for py in glob.glob(str(ROOT / "scripts" / "*.py")):
    datas.append((py, "scripts"))

binaries = [(str(NATIVE / n), "native") for n in
            ("vcs_d3d11.dll", "vcs_mfcap.dll", "vcs_wgc.dll", "vcs_dupl.dll", "vcam_dshow.dll", "vcam_mf.dll",
             "vcam-setup.exe", "mf_client_test.exe")]
# the OBS plugin is GPL-2.0-or-later (native/obs-plugin/README.md): its complete source goes with the binary
for f in ("vcstudio-obs.c", "vcs_frame_shm.h", "COPYING", "README.md"):
    datas.append((str(ROOT / "native" / "obs-plugin" / f), "obs-plugin/source"))
for f in ("build_obs_plugin_win.ps1", "build_obs_plugin.sh"):
    datas.append((str(ROOT / "scripts" / f), "obs-plugin/source/scripts"))
# data, not a binary: its imports (obs.dll, obs-frontend-api.dll) exist only inside OBS
datas.append((str(ROOT / "build" / "obs-plugin" / "win" / "vcstudio-obs.dll"), "obs-plugin"))
missing = [b for b, _ in binaries + datas[-1:] if not Path(b).exists()]
if missing:
    raise SystemExit("build the native parts first (scripts/build_native_win.ps1, build_obs_plugin_win.ps1): "
                     + ", ".join(missing))

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules  # noqa: E402
hidden = ["psutil", "cv2", "scipy.optimize", "onnxruntime", "av", "PIL",
          "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets"]
hidden += collect_submodules("vcstudio")          # pages and dialogs are imported lazily inside functions
binaries += collect_dynamic_libs("onnxruntime")   # onnxruntime_providers_*.dll, DirectML.dll
datas += collect_data_files("onnxruntime", includes=["**/*.dll"])

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "PyQt5", "PyQt6", "torch", "ultralytics",
              "objc", "Foundation", "AppKit", "Quartz", "CoreML", "AVFoundation", "Vision", "Metal",
              "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore", "PySide6.QtQuick",
              "PySide6.QtQml", "PySide6.QtPdf", "PySide6.QtMultimedia", "PySide6.QtCharts"],
    noarchive=False,
)
# GPL-only Qt modules (open-source Qt licence) must not ship in a proprietary app. PyInstaller's PySide6 hook adds
# the virtual keyboard input-context plugin (and with it Qt6VirtualKeyboard*.dll) on its own; the app uses none of
# these. scripts/check_licenses.py --app verifies the result.
import re  # noqa: E402
_GPL_QT = re.compile(r"Qt6(Charts|DataVisualization|Graphs|VirtualKeyboard|Quick3D|Lottie|NetworkAuth|Coap|Mqtt|OpcUa|"
                     r"HttpServer|WaylandCompositor)|qtvirtualkeyboard", re.I)
a.binaries = [b for b in a.binaries if not _GPL_QT.search(b[0]) and not _GPL_QT.search(b[1])]
a.datas = [d for d in a.datas if not _GPL_QT.search(d[0]) and not _GPL_QT.search(d[1])]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="vcstudio", debug=False, strip=False, upx=False,
          console=False, icon=str(ROOT / "packaging" / "AppIcon.ico"),
          version=str(ROOT / "build" / "win_version_info.txt"))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="AI Virtual Camera Studio")
