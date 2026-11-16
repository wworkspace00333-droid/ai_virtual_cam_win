<#
Build PyAV against an LGPL-only FFmpeg and install it into the app's venv (Windows).

Why: the PyAV wheels from PyPI bundle libx264 / libx265 (GPL-2.0+) next to FFmpeg. The app only decodes video and
never needs those encoders, but shipping the DLLs puts GPL code into a proprietary installer. This script builds the
same PyAV version from source against BtbN's "lgpl-shared" FFmpeg build (configured with --enable-version3,
--disable-libx264 --disable-libx265, no --enable-gpl), and copies only the FFmpeg DLLs into the av package.

  powershell -ExecutionPolicy Bypass -File scripts\build_pyav_lgpl_win.ps1 [-Python C:\vcs-env\Scripts\python.exe] [-Work C:\vcs-build]

Inputs (downloaded by hand or by this script, SHA-256 checked):
  FFmpeg  https://github.com/BtbN/FFmpeg-Builds/releases  ffmpeg-n9.0-latest-win64-lgpl-shared-9.0.zip
          ("latest" assets are rebuilt daily: pass -FfmpegZip with the file you reviewed and its hash with -FfmpegSha256)
  PyAV    https://pypi.org/project/av/19.0.2/  av-19.0.2.tar.gz
Needs: Visual Studio 2022 Build Tools (C++ x64). Cython and setuptools go into a separate build venv, not the app venv.
Afterwards: python scripts\check_licenses.py must report no GPL component.
#>
param(
    [string]$Python = "C:\vcs-env\Scripts\python.exe",
    [string]$Work = "C:\vcs-build",                       # short path: the FFmpeg archive has long names
    [string]$PyavVersion = "19.0.2",
    [string]$PyavSha256 = "bebbc52598886480f4d861c575dc2b020785d6c04e1308680ddcc545e1b94c66",
    [string]$FfmpegZip = "",
    [string]$FfmpegSha256 = ""
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
function Check($what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" } }
function Sha($p) { (Get-FileHash $p -Algorithm SHA256).Hash.ToLower() }

New-Item -ItemType Directory -Force $Work | Out-Null      # a fresh CI machine has no work folder yet

# ---- FFmpeg (LGPL)
$ffRoot = Join-Path $Work "ffmpeg-lgpl"
if (-not $FfmpegZip) {
    $FfmpegZip = Join-Path $Work "ffmpeg-lgpl.zip"
    if (-not (Test-Path $FfmpegZip)) {
        Invoke-WebRequest -UseBasicParsing "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n9.0-latest-win64-lgpl-shared-9.0.zip" -OutFile $FfmpegZip
    }
}
if ($FfmpegSha256 -and (Sha $FfmpegZip) -ne $FfmpegSha256.ToLower()) { throw "FFmpeg archive SHA-256 mismatch" }
Write-Host "FFmpeg archive SHA-256: $(Sha $FfmpegZip)"
if (-not (Test-Path $ffRoot)) { New-Item -ItemType Directory $ffRoot | Out-Null; tar -xf $FfmpegZip -C $ffRoot; Check "unpacking FFmpeg" }
$ff = (Get-ChildItem $ffRoot -Directory | Select-Object -First 1).FullName
$conf = (& "$ff\bin\ffmpeg.exe" -hide_banner -buildconf 2>&1) -join " "
if ($conf -match "--enable-gpl|--enable-libx264|--enable-libx265|--enable-nonfree") { throw "this FFmpeg build is not LGPL-only" }

# ---- PyAV source
$src = Join-Path $Work "pyav-src"
New-Item -ItemType Directory -Force $src | Out-Null
$tgz = Join-Path $src "av-$PyavVersion.tar.gz"
if (-not (Test-Path $tgz)) {
    $meta = Invoke-RestMethod "https://pypi.org/pypi/av/$PyavVersion/json"
    $u = ($meta.urls | Where-Object { $_.filename -eq "av-$PyavVersion.tar.gz" }).url
    Invoke-WebRequest -UseBasicParsing $u -OutFile $tgz
}
if ((Sha $tgz) -ne $PyavSha256) { throw "PyAV source SHA-256 mismatch" }
$pyav = Join-Path $src "av-$PyavVersion"
if (-not (Test-Path $pyav)) { tar -xzf $tgz -C $src; Check "unpacking PyAV" }

# ---- build venv (Cython stays out of the app venv)
$bv = Join-Path $Work "pyav-venv"
if (-not (Test-Path "$bv\Scripts\python.exe")) { & $Python -m venv $bv; Check "build venv" }
& "$bv\Scripts\python.exe" -m pip install -q "cython>=3.3,<4" "setuptools>=78" wheel; Check "build tools"

# ---- compile with MSVC
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
$vcvars = Join-Path $vs "VC\Auxiliary\Build\vcvars64.bat"
$dist = Join-Path $Work "pyav-dist"
Remove-Item -Recurse -Force $dist -ErrorAction SilentlyContinue
& cmd.exe /c "call `"$vcvars`" >nul && cd /d `"$pyav`" && `"$bv\Scripts\python.exe`" setup.py -q bdist_wheel --ffmpeg-dir=`"$ff`" -d `"$dist`""
Check "PyAV build"
$whl = Get-ChildItem $dist -Filter "av-*.whl" | Select-Object -First 1
Write-Host "built $($whl.Name)"

# ---- install into the app venv, then put the FFmpeg DLLs next to the extension modules
& $Python -m pip uninstall -y -q av
& $Python -m pip install -q --no-deps $whl.FullName; Check "installing PyAV"
$site = (& $Python -c "import sysconfig; print(sysconfig.get_paths()['purelib'])").Trim()
Copy-Item "$ff\bin\*.dll" (Join-Path $site "av") -Force
Copy-Item "$ff\LICENSE.txt" (Join-Path $site "av\FFMPEG_LICENSE.txt") -Force -ErrorAction SilentlyContinue
& $Python -c "import av; m=av._core.library_meta['libavcodec']; print('PyAV', av.__version__, 'FFmpeg', av.ffmpeg_version_info, m['license'])"; Check "PyAV import"
