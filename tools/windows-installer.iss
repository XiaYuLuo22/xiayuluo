; Compile only the already-built Windows application, never the source checkout.
#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef AppSource
  #error AppSource is required
#endif

[Setup]
AppId=com.xiayuluo.assistant.windows
AppName=夏鱼洛
AppVersion={#AppVersion}
AppPublisher=Xiayuluo
DefaultDirName={code:DefaultDirectory}
DefaultGroupName=夏鱼洛
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputBaseFilename=Xiayuluo-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\Xiayuluo.exe
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: checkedonce

[Files]
Source: "{#AppSource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\夏鱼洛"; Filename: "{app}\Xiayuluo.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\夏鱼洛"; Filename: "{app}\Xiayuluo.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\Xiayuluo.exe"; Description: "Launch Xiayuluo"; Flags: nowait postinstall skipifsilent

[Code]
function DefaultDirectory(Param: String): String;
begin
  if DirExists('E:\') then
    Result := 'E:\Xiayuluo'
  else
    Result := ExpandConstant('{localappdata}\Programs\Xiayuluo');
end;
