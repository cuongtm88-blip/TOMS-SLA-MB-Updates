#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

[Setup]
AppId={{DA79E79B-2E17-4AAF-A723-BDF84D8E1A2B}
AppName=TOMS SLA MB
AppVersion={#MyAppVersion}
AppPublisher=cuongtm88-blip
DefaultDirName={localappdata}\Programs\TOMS SLA MB
DefaultGroupName=TOMS SLA MB
PrivilegesRequired=lowest
OutputDir=dist-installer
OutputBaseFilename=TOMS-SLA-MB
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\TOMS-SLA-MB.exe

[Files]
Source: "dist\TOMS-SLA-MB\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\TOMS SLA MB"; Filename: "{app}\TOMS-SLA-MB.exe"
Name: "{autodesktop}\TOMS SLA MB"; Filename: "{app}\TOMS-SLA-MB.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Tạo biểu tượng ngoài màn hình"; GroupDescription: "Biểu tượng bổ sung:"; Flags: checkedonce

[Run]
Filename: "{app}\TOMS-SLA-MB.exe"; Description: "Mở TOMS SLA MB"; Flags: nowait postinstall skipifsilent

[Code]
function InitializeSetup(): Boolean;
var
  ReadyFile: String;
begin
  ReadyFile := GetEnv('TOMS_UPDATE_READY_FILE');
  if ReadyFile <> '' then
    SaveStringToFile(ReadyFile, 'installer-ready', False);
  Result := True;
end;
