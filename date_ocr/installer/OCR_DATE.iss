#ifndef PayloadDir
  #error PayloadDir must point to the tested portable payload
#endif
#ifndef InitialConfig
  #error InitialConfig must point to the sanitized fresh-install config
#endif

[Setup]
AppId={{3C38704D-89B2-44FB-ABA1-0AF396C43EB8}
AppName=OCR DATE - Hikrobot RS485
AppVersion=2026.10.29.3
AppVerName=OCR DATE - Hikrobot RS485 2026.10.29.3
AppPublisher=OCR DATE
DefaultDirName={code:DefaultInstallDirectory}
DefaultGroupName=OCR DATE - Hikrobot RS485
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputBaseFilename=OCR_DATE_Setup_2026-10-29
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\OCR_DATE.exe
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
InfoBeforeFile=HUONG_DAN_CAI_DAT.txt

[Tasks]
Name: desktopicon; Description: "Tao loi tat OCR DATE tren Desktop"; Flags: checkedonce

[Dirs]
Name: "{app}\data\debug"; Flags: uninsneveruninstall
Name: "{app}\data\logs"; Flags: uninsneveruninstall

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Excludes: "\config\config.json,\data\debug\*,\data\logs\*,\README.md"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#InitialConfig}"; DestDir: "{app}\config"; DestName: config.json; Flags: onlyifdoesntexist uninsneveruninstall
Source: "HUONG_DAN_CAI_DAT.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{userdesktop}\OCR DATE - Hikrobot RS485"; Filename: "{app}\OCR_DATE.exe"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{group}\OCR DATE"; Filename: "{app}\OCR_DATE.exe"; WorkingDir: "{app}"
Name: "{group}\Huong dan cai dat"; Filename: "{app}\HUONG_DAN_CAI_DAT.txt"

[Run]
Filename: "{app}\OCR_DATE.exe"; Description: "Mo OCR DATE"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent unchecked

[Code]
function DefaultInstallDirectory(Param: String): String;
begin
  if DirExists('D:\') then
    Result := 'D:\Apps\OCR_DATE'
  else
    Result := ExpandConstant('{localappdata}\Programs\OCR_DATE');
end;
