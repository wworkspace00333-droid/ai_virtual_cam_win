<#
Build the Windows native components into build\native\ with MSVC (Visual Studio 2022 / Build Tools, C++ x64):
  vcs_d3d11.dll      Direct3D 11 compositor (native\win\d3d11)
More components (virtual camera DLLs, vcam-setup.exe) are added to this script as they are ported.

  scripts\build_native_win.ps1 [-Config Release|Debug]
#>
param([ValidateSet("Release", "Debug")][string]$Config = "Release")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$out = Join-Path $root "build\native"
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
