<#
Authenticode signing for the Windows build (dot-sourced by build_installer_win.ps1, or run directly):

  powershell -File scripts\sign_win.ps1 <file> [<file> ...]

Nothing secret lives in the repository: the certificate is chosen with environment variables. Signing is OFF when none
of them is set (local builds stay possible), unless VCS_SIGN_REQUIRED=1 (release builds), which then fails the build.

  VCS_SIGN_CERT_THUMBPRINT  SHA-1 thumbprint of a code-signing certificate in the Windows certificate store
                            (CurrentUser\My or LocalMachine\My; also hardware tokens / HSMs that expose their key there)
  VCS_SIGN_PFX              path to a .pfx file  (+ VCS_SIGN_PFX_PASSWORD; prefer the store / a token in CI)
  VCS_SIGN_DLIB, VCS_SIGN_DMDF   Azure Trusted Signing / Artifact Signing: path to the signing client DLL and its
                            metadata JSON (signtool /dlib /dmdf); authentication comes from the Azure environment
  VCS_SIGN_TIMESTAMP_URL    RFC 3161 time-stamp server (default http://timestamp.digicert.com); signatures stay valid
                            after the certificate expires only with a time stamp
  VCS_SIGNTOOL              signtool.exe to use (default: the newest one in the Windows 10/11 SDK)
  VCS_SIGN_DESCRIPTION      description shown in the UAC prompt (default "AI Virtual Camera Studio")

Only our own binaries are signed (camera DLLs, setup tool, compositor, capture, OBS plugin, vcstudio.exe, the installer
and its uninstaller); third-party DLLs keep their vendors' signatures.
#>

function Get-SignTool {
    if ($env:VCS_SIGNTOOL) { return $env:VCS_SIGNTOOL }
    $kits = "${env:ProgramFiles(x86)}\Windows Kits\10\bin"
    $st = Get-ChildItem $kits -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match "\\x64\\" } | Sort-Object FullName -Descending | Select-Object -First 1
    if (-not $st) { throw "signtool.exe not found (install the Windows SDK or set VCS_SIGNTOOL)" }
    return $st.FullName
}

function Test-SigningConfigured {
    return [bool]($env:VCS_SIGN_CERT_THUMBPRINT -or $env:VCS_SIGN_PFX -or $env:VCS_SIGN_DLIB)
}

# signtool arguments without the file name (also used for Inno Setup's SignTool directive)
function Get-SignArgs {
    $ts = if ($env:VCS_SIGN_TIMESTAMP_URL) { $env:VCS_SIGN_TIMESTAMP_URL } else { "http://timestamp.digicert.com" }
    $desc = if ($env:VCS_SIGN_DESCRIPTION) { $env:VCS_SIGN_DESCRIPTION } else { "AI Virtual Camera Studio" }
    $a = @("sign", "/fd", "SHA256", "/td", "SHA256", "/tr", $ts, "/d", $desc)
    if ($env:VCS_SIGN_DLIB) {
        $a += @("/dlib", $env:VCS_SIGN_DLIB, "/dmdf", $env:VCS_SIGN_DMDF)
    } elseif ($env:VCS_SIGN_PFX) {
        $a += @("/f", $env:VCS_SIGN_PFX)
        if ($env:VCS_SIGN_PFX_PASSWORD) { $a += @("/p", $env:VCS_SIGN_PFX_PASSWORD) }
    } else {
        $a += @("/sha1", $env:VCS_SIGN_CERT_THUMBPRINT)
    }
    return $a
}

function Invoke-Sign([string[]]$Files) {
    if (-not (Test-SigningConfigured)) {
        if ($env:VCS_SIGN_REQUIRED -eq "1") { throw "VCS_SIGN_REQUIRED=1 but no signing certificate is configured" }
        Write-Host "signing: off (no VCS_SIGN_* certificate configured)" -ForegroundColor Yellow
        return
    }
    $st = Get-SignTool
    $signArgs = Get-SignArgs
    foreach ($f in $Files) {
        if (-not (Test-Path $f)) { throw "signing: missing $f" }
        & $st @signArgs $f | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "signing failed: $f" }
        $s = Get-AuthenticodeSignature $f
        if (-not $s.SignerCertificate) { throw "no signature on $f after signing" }
        Write-Host ("signed  {0}  [{1}; time stamp: {2}]" -f (Split-Path $f -Leaf), $s.Status,
                    $(if ($s.TimeStamperCertificate) { "yes" } else { "NO" }))
    }
}

if ($MyInvocation.InvocationName -ne ".") {          # run directly: sign the files given on the command line
    $ErrorActionPreference = "Stop"
    Invoke-Sign $args
}
