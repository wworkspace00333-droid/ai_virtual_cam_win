# PyInstaller spec: AI Virtual Camera Studio for Windows (built by scripts/build_installer_win.ps1).
# One-folder build: dist\AI Virtual Camera Studio\vcstudio.exe + _internal\ (sys._MEIPASS):
#   native\      vcs_d3d11.dll, vcs_mfcap.dll, vcam_dshow.dll, vcam_mf.dll, vcam-setup.exe, mf_client_test.exe
#   models\      *.onnx + *.json (YOLO11 detection / segmentation, YuNet faces)
#   obs-plugin\  vcstudio-obs.dll
#   sample_data\ sample photo, logo, clip, coco16 accuracy set
#   scripts\     developer tools (--run-script)
# The installer (packaging/windows/installer.iss) puts the folder in Program Files and registers the camera
# from there with native\vcam-setup.exe.
import glob
import os
from pathlib import Path

ROOT = Path(SPECPATH).parent
VERSION = os.environ.get("VCS_VERSION", "0.3.2")
NATIVE = ROOT / "build" / "native"

datas = []
for mp in sorted((ROOT / "models").glob("*.onnx")):
    datas.append((str(mp), "models"))
    if mp.with_suffix(".json").exists():
        datas.append((str(mp.with_suffix(".json")), "models"))
for name in ("bus.jpg", "logo_alpha.png", "clip_h264.mp4"):
    datas.append((str(ROOT / "tests" / "data" / name), "sample_data"))
for sub in ("images", "labels"):
    datas.append((str(ROOT / "tests" / "data" / "coco16" / sub), f"sample_data/coco16/{sub}"))
for py in glob.glob(str(ROOT / "scripts" / "*.py")):
    datas.append((py, "scripts"))

binaries = [(str(NATIVE / n), "native") for n in
            ("vcs_d3d11.dll", "vcs_mfcap.dll", "vcam_dshow.dll", "vcam_mf.dll", "vcam-setup.exe", "mf_client_test.exe")]
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
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="vcstudio", debug=False, strip=False, upx=False,
          console=False, icon=str(ROOT / "packaging" / "AppIcon.ico"),
          version=str(ROOT / "build" / "win_version_info.txt"))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="AI Virtual Camera Studio")
