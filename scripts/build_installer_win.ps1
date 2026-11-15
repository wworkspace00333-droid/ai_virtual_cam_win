# Build the Windows installer: <Out>\AIVirtualCameraStudio-Setup-<version>.exe (default C:\vcs-build)
#
#   powershell -ExecutionPolicy Bypass -File scripts\build_installer_win.ps1 [-SkipNative] [-Python C:\vcs-env\Scripts\python.exe] [-Out C:\vcs-build]
#
# Steps: native parts (camera, D3D11, setup tool) -> OBS plugin -> icon + version resource -> PyInstaller folder
# -> self-check of the frozen app -> Inno Setup 6 (ISCC.exe). Needs: VS 2022 Build Tools (C++), Python venv with
# requirements-windows.txt + pyinstaller, OBS Studio (for the plugin's import libraries), Inno Setup 6.
param(
    [switch]$SkipNative,
    [string]$Python = "C:\vcs-env\Scripts\python.exe",
    # short folder: PySide6 / Pillow paths inside the app folder exceed the 260-character limit under a deep path
    [string]$Out = "C:\vcs-build"
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Step($t) { Write-Host "`n== $t" -ForegroundColor Cyan }
function Check($what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" } }

$Version = (& $Python -c "import vcstudio; print(vcstudio.__version__)").Trim(); Check "reading the version"
Write-Host "AI Virtual Camera Studio $Version"

if (-not $SkipNative) {
    Step "native parts"
    & powershell -NoProfile -ExecutionPolicy Bypass -File "$Root\scripts\build_native_win.ps1"; Check "build_native_win.ps1"
    Step "OBS plugin"
    & powershell -NoProfile -ExecutionPolicy Bypass -File "$Root\scripts\build_obs_plugin_win.ps1"; Check "build_obs_plugin_win.ps1"
}

Step "icon and version resource"
& $Python "$Root\packaging\make_icon_win.py"; Check "make_icon_win.py"
$v = ($Version.Split(".") + @("0", "0", "0", "0"))[0..3] -join ", "
New-Item -ItemType Directory -Force "$Root\build" | Out-Null
@"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($v), prodvers=($v), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'AI Virtual Camera Studio'),
    StringStruct('FileDescription', 'AI Virtual Camera Studio'),
    StringStruct('FileVersion', '$Version'),
    StringStruct('InternalName', 'vcstudio'),
    StringStruct('OriginalFilename', 'vcstudio.exe'),
    StringStruct('ProductName', 'AI Virtual Camera Studio'),
    StringStruct('ProductVersion', '$Version')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
"@ | Set-Content -Encoding utf8 "$Root\build\win_version_info.txt"

Step "PyInstaller"
$env:VCS_VERSION = $Version
& $Python -m PyInstaller --noconfirm --clean --distpath "$Out\dist" --workpath "$Out\work" `
    "$Root\packaging\vcstudio_win.spec"; Check "PyInstaller"
$App = "$Out\dist\AI Virtual Camera Studio"

Step "self-check of the frozen app"
$p = Start-Process -FilePath "$App\vcstudio.exe" -ArgumentList "--self-check", "`"$Root\build\selfcheck.json`"" `
    -Wait -PassThru -NoNewWindow
& $Python -c "import json,sys; d=json.load(open(sys.argv[1], encoding='utf-8')); [print(('PASS' if r['ok'] else ('FAIL' if r['required'] else 'WARN')), r['check'], '-', r['detail'][:90]) for r in d['results']]; print('frozen:', d['frozen'])" "$Root\build\selfcheck.json"
if ($p.ExitCode -ne 0) { throw "the frozen app failed its self-check (build\selfcheck.json)" }

Step "Inno Setup"
$Iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Iscc) { throw "Inno Setup 6 not found (https://jrsoftware.org/isdl.php)" }
& $Iscc "/DAppVersion=$Version" "/DSourceDir=$App" "/DOutputDir=$Out" "$Root\packaging\windows\installer.iss"
Check "ISCC"
$Setup = Get-Item "$Out\AIVirtualCameraStudio-Setup-$Version.exe"
Write-Host ("`nOK: {0} ({1:N0} MB)" -f $Setup.FullName, ($Setup.Length / 1MB)) -ForegroundColor Green
