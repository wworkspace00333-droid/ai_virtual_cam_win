<#
Build the Windows native components into build\native\ with MSVC (Visual Studio 2022 / Build Tools, C++ x64):
  vcs_d3d11.dll      Direct3D 11 compositor (native\win\d3d11)
  vcs_mfcap.dll      Media Foundation camera capture (native\win\capture)
  vcam_dshow.dll, vcam_mf.dll, vcam-setup.exe, mf_client_test.exe   the virtual camera (native\win\vcam)

  scripts\build_native_win.ps1 [-Config Release|Debug] [-Test] [-Out <folder>]   (-Out: default build\native)
#>
param([ValidateSet("Release", "Debug")][string]$Config = "Release", [switch]$Test, [string]$Out = "")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$out = if ($Out) { $Out } else { Join-Path $root "build\native" }
$obj = Join-Path $root "build\obj"
New-Item -ItemType Directory -Force $out, $obj | Out-Null

$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vs) { throw "Visual Studio C++ x64 build tools not found" }
$vcvars = Join-Path $vs "VC\Auxiliary\Build\vcvars64.bat"
$env:Path = "$(Split-Path $vswhere);$env:Path"

function Invoke-VS([string]$cmd) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & cmd.exe /c "call `"$vcvars`" >nul 2>nul && $cmd" 2>&1 | ForEach-Object { "$_" }
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prev
    if ($code -ne 0) { throw "failed ($code): $cmd" }
}

$opt = if ($Config -eq "Release") { "/O2 /MT /DNDEBUG" } else { "/Od /Zi /MTd" }
$common = "/nologo /std:c++17 /EHsc /W4 /permissive- /utf-8 $opt"

# vcs_d3d11.dll (static CRT: no VC++ redistributable needed next to the app)
$src = Join-Path $root "native\win\d3d11\vcs_d3d11.cpp"
Invoke-VS ("cl $common /LD `"$src`" /Fo`"$obj\\`" /link /OUT:`"$out\vcs_d3d11.dll`" " +
           "/IMPLIB:`"$obj\vcs_d3d11.lib`" d3d11.lib dxgi.lib d3dcompiler.lib")
Write-Host "OK $out\vcs_d3d11.dll"

# vcs_mfcap.dll: Media Foundation capture (the opencv-python wheels have no MSMF backend)
$src = Join-Path $root "native\win\capture\vcs_mfcap.cpp"
Invoke-VS ("cl $common /LD `"$src`" /Fo`"$obj\\`" /link /OUT:`"$out\vcs_mfcap.dll`" /IMPLIB:`"$obj\vcs_mfcap.lib`"")
Write-Host "OK $out\vcs_mfcap.dll"

# DirectShow BaseClasses (Microsoft sample code, MIT) as a static library; old code: lenient flags
$bc = Join-Path $root "native\win\third_party\baseclasses"
$bcobj = Join-Path $obj "baseclasses"
New-Item -ItemType Directory -Force $bcobj | Out-Null
$bcflags = "/nologo /EHsc /W1 /utf-8 $opt /DUNICODE /D_UNICODE /DWIN32 /D_WIN32_WINNT=0x0A00 /I`"$bc`""
Invoke-VS ("cl $bcflags /c `"$bc\*.cpp`" /Fo`"$bcobj\\`"")
Invoke-VS ("lib /nologo /OUT:`"$obj\strmbase.lib`" `"$bcobj\*.obj`"")

# vcam_dshow.dll: "AI Virtual Camera" DirectShow source filter
$vc = Join-Path $root "native\win\vcam"
$vcflags = "/nologo /std:c++17 /EHsc /W3 /utf-8 $opt /DUNICODE /D_UNICODE /DWIN32 /D_WIN32_WINNT=0x0A00 /I`"$bc`" /I`"$vc`""
Invoke-VS ("cl $vcflags /LD `"$vc\dshow\vcam_dshow.cpp`" `"$vc\vcam_frames.cpp`" /Fo`"$obj\\`" " +
           "/link /DEF:`"$vc\dshow\vcam_dshow.def`" /OUT:`"$out\vcam_dshow.dll`" /IMPLIB:`"$obj\vcam_dshow.lib`" " +
           "`"$obj\strmbase.lib`" strmiids.lib winmm.lib ole32.lib oleaut32.lib advapi32.lib user32.lib gdi32.lib wtsapi32.lib")
Write-Host "OK $out\vcam_dshow.dll"

# vcam_mf.dll: "AI Virtual Camera" Media Foundation source (Windows 11 virtual camera)
Invoke-VS ("cl /nologo /std:c++17 /EHsc /W3 /utf-8 $opt /DUNICODE /D_UNICODE /D_WIN32_WINNT=0x0A00 /I`"$vc`" /LD " +
           "`"$vc\mf\vcam_mf.cpp`" `"$vc\vcam_frames.cpp`" /Fo`"$obj\\`" /link /DEF:`"$vc\mf\vcam_mf.def`" " +
           "/OUT:`"$out\vcam_mf.dll`" /IMPLIB:`"$obj\vcam_mf.lib`" mfplat.lib mfuuid.lib mf.lib ole32.lib advapi32.lib " +
           "user32.lib gdi32.lib wtsapi32.lib")
Write-Host "OK $out\vcam_mf.dll"

# vcam-setup.exe: registers / removes the camera; requires administrator rights (UAC manifest)
Invoke-VS ("cl /nologo /std:c++17 /EHsc /W4 /utf-8 $opt /DUNICODE /D_UNICODE `"$vc\setup\vcam_setup.cpp`" /Fo`"$obj\\`" " +
           "/link /OUT:`"$out\vcam-setup.exe`" /MANIFEST:EMBED /MANIFESTUAC:`"level='requireAdministrator' uiAccess='false'`" " +
           "advapi32.lib mfplat.lib ole32.lib")
Write-Host "OK $out\vcam-setup.exe"

# mf_client_test.exe: reads the Media Foundation camera like a client (in-process or through Frame Server)
Invoke-VS ("cl /nologo /std:c++17 /EHsc /W3 /utf-8 $opt /DUNICODE /D_UNICODE `"$vc\test\mf_client_test.cpp`" /Fo`"$obj\\`" " +
           "/link /OUT:`"$out\mf_client_test.exe`"")
Write-Host "OK $out\mf_client_test.exe"

if ($Test) {
    Invoke-VS ("cl $vcflags `"$vc\test\vcam_frames_test.cpp`" `"$vc\vcam_frames.cpp`" /Fo`"$obj\\`" " +
               "/link /OUT:`"$obj\vcam_frames_test.exe`" user32.lib gdi32.lib wtsapi32.lib")
    & "$obj\vcam_frames_test.exe"
    if ($LASTEXITCODE -ne 0) { throw "vcam_frames_test failed" }
}
