; Inno Setup 6 script: AI Virtual Camera Studio for Windows 10/11 x64 (built by scripts\build_installer_win.ps1).
; Installs the PyInstaller folder (dist\AI Virtual Camera Studio) into Program Files, registers the app's own
; virtual camera with native\vcam-setup.exe (Windows 11: Media Foundation camera, DirectShow if that cannot start;
; Windows 10: DirectShow), optionally installs the OBS plugin, and undoes all of it on uninstall. User data
; (%LOCALAPPDATA%\AIVirtualCameraStudio: settings, profiles, imported assets) is kept on uninstall.

#ifndef AppVersion
  #define AppVersion "0.3.2"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\AI Virtual Camera Studio"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist"
#endif
#define AppName "AI Virtual Camera Studio"
#define AppExe "vcstudio.exe"
#define Native "{app}\_internal\native"
#define ObsPluginDir "{commonappdata}\obs-studio\plugins\vcstudio-obs"

[Setup]
AppId={{6B0E7C1A-3F5D-4B8E-9A61-2C4D8E0F7A35}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=AI Virtual Camera Studio
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#OutputDir}
OutputBaseFilename=AIVirtualCameraStudio-Setup-{#AppVersion}
SetupIconFile=..\AppIcon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"

[CustomMessages]
en.ObsPlugin=Install the OBS plugin (direct frame transfer from OBS, no OBS Virtual Camera needed)
ru.ObsPlugin=Установить плагин OBS (прямая передача кадров из OBS, без OBS Virtual Camera)
en.CameraTask=Register the "AI Virtual Camera" device
ru.CameraTask=Зарегистрировать камеру «AI Virtual Camera»
en.DShowToo=Also install the DirectShow camera: any number of apps can use it at the same time, and DirectShow apps (OBS, Discord) get 60 fps
ru.DShowToo=Также установить DirectShow-камеру: ею могут одновременно пользоваться любые приложения, а DirectShow-приложения (OBS, Discord) получают 60 кадров/с
en.Registering=Registering the AI Virtual Camera...
ru.Registering=Регистрация камеры AI Virtual Camera...
en.CamFallback=The Windows 11 camera could not start (is "Let apps access your camera" on in Settings > Privacy & security > Camera?), so the DirectShow camera was installed instead. It works in most apps; to switch later, allow camera access and click Install on the app's Virtual Camera page.
ru.CamFallback=Камера Windows 11 не запустилась (включён ли «Разрешить приложениям доступ к камере» в Параметры > Конфиденциальность и защита > Камера?), поэтому установлена DirectShow-камера. Она работает в большинстве приложений; чтобы переключиться позже, разрешите доступ к камере и нажмите «Установить» на странице «Виртуальная камера» в приложении.
en.CamFailed=The AI Virtual Camera could not be registered (code %1). The app is installed; open its Virtual Camera page to retry and see the details.
ru.CamFailed=Не удалось зарегистрировать камеру AI Virtual Camera (код %1). Приложение установлено; откройте в нём страницу «Виртуальная камера», чтобы повторить и увидеть подробности.

[Tasks]
Name: "camera"; Description: "{cm:CameraTask}"
Name: "camera\dshowtoo"; Description: "{cm:DShowToo}"; Check: IsWindows11
Name: "obsplugin"; Description: "{cm:ObsPlugin}"; Check: ObsInstalled
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; the camera DLLs may be loaded by Frame Server / client apps during an update: replace them after a restart then
Source: "{#SourceDir}\_internal\native\*"; DestDir: "{#Native}"; Flags: ignoreversion restartreplace uninsrestartdelete
Source: "{#SourceDir}\*"; DestDir: "{app}"; Excludes: "_internal\native\*"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceDir}\_internal\obs-plugin\vcstudio-obs.dll"; DestDir: "{#ObsPluginDir}\bin\64bit"; Tasks: obsplugin; Flags: ignoreversion restartreplace uninsrestartdelete

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: dirifempty; Name: "{#ObsPluginDir}\bin\64bit"
Type: dirifempty; Name: "{#ObsPluginDir}\bin"
Type: dirifempty; Name: "{#ObsPluginDir}"

[Code]
function ObsInstalled: Boolean;
begin
  Result := FileExists(ExpandConstant('{commonpf64}\obs-studio\bin\64bit\obs64.exe'))
    or RegKeyExists(HKLM64, 'SOFTWARE\OBS Studio') or DirExists(ExpandConstant('{commonappdata}\obs-studio'));
end;

{ Windows 11 (build 22000+): the Media Foundation camera exists; Windows 10 always gets the DirectShow camera }
function IsWindows11: Boolean;
var
  V: TWindowsVersion;
begin
  GetWindowsVersionEx(V);
  Result := (V.Major > 10) or ((V.Major = 10) and (V.Build >= 22000));
end;

function RunSetupTool(const Dir, Params: String): Integer;
var
  Tool: String;
  Code: Integer;
begin
  Result := -1;
  Tool := AddBackslash(Dir) + 'vcam-setup.exe';
  if not FileExists(Tool) then begin
    Log('vcam-setup.exe not found in ' + Dir);
    exit;
  end;
  if Exec(Tool, Params, Dir, SW_HIDE, ewWaitUntilTerminated, Code) then
    Result := Code
  else
    Log('vcam-setup.exe could not start: ' + SysErrorMessage(Code));
  Log('vcam-setup.exe ' + Params + ' -> ' + IntToStr(Result));
end;

{ Updating: unregister the camera of the installed version first, so Frame Server lets go of its DLLs }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  OldDir: String;
begin
  Result := '';
  if RegQueryStringValue(HKLM64, 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{6B0E7C1A-3F5D-4B8E-9A61-2C4D8E0F7A35}_is1',
                         'InstallLocation', OldDir) then
    RunSetupTool(AddBackslash(OldDir) + '_internal\native', '--uninstall');
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  rc: Integer;
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('camera') then begin
    WizardForm.StatusLabel.Caption := CustomMessage('Registering');
    if WizardIsTaskSelected('camera\dshowtoo') then
      rc := RunSetupTool(ExpandConstant('{#Native}'), '--install --both')
    else
      rc := RunSetupTool(ExpandConstant('{#Native}'), '--install');
    if rc = 3 then
      SuppressibleMsgBox(CustomMessage('CamFallback'), mbInformation, MB_OK, IDOK)
    else if rc <> 0 then
      SuppressibleMsgBox(FmtMessage(CustomMessage('CamFailed'), [IntToStr(rc)]), mbError, MB_OK, IDOK);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then         { before the files are removed }
    RunSetupTool(ExpandConstant('{#Native}'), '--uninstall');
end;
