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
function TrainingGetFileAttributes(Path: String): LongWord;
  external 'GetFileAttributesW@kernel32.dll stdcall';

function TrainingAbsolutePath(Path: String): Boolean;
begin
  Result := ((Length(Path) >= 3) and (Copy(Path, 2, 1) = ':') and
    ((Copy(Path, 3, 1) = '\') or (Copy(Path, 3, 1) = '/'))) or
    ((Length(Path) >= 5) and (Copy(Path, 1, 2) = '\\'));
end;

function TrainingNormalizePath(Path: String): String;
begin
  Result := Lowercase(ExpandFileName(Path));
  StringChangeEx(Result, '/', '\', True);
  while (Length(Result) > 3) and (Copy(Result, Length(Result), 1) = '\') do
    Delete(Result, Length(Result), 1);
end;

function TrainingPathsOverlap(First, Second: String): Boolean;
var
  Left, Right: String;
begin
  Left := AddBackslash(TrainingNormalizePath(First));
  Right := AddBackslash(TrainingNormalizePath(Second));
  Result := (Pos(Left, Right) = 1) or (Pos(Right, Left) = 1);
end;

function TrainingHasReparseParent(Path: String): Boolean;
var
  Current, Parent: String;
  Attributes: LongWord;
begin
  Result := False;
  Current := TrainingNormalizePath(Path);
  while Current <> '' do
  begin
    Attributes := TrainingGetFileAttributes(Current);
    if (Attributes <> $FFFFFFFF) and ((Attributes and $400) <> 0) then
    begin
      Result := True;
      Exit;
    end;
    Parent := ExtractFileDir(Current);
    if CompareText(Parent, Current) = 0 then
      Exit;
    Current := Parent;
  end;
end;

function TrainingProtectedLocationError(InstallPath, DataPath: String): String;
begin
  Result := '';
  if DataPath = '' then
    Exit;
  if not TrainingAbsolutePath(DataPath) then
    Result := 'The configured Xiayuluo training directory must be an absolute path.'
  else if TrainingHasReparseParent(DataPath) then
    Result := 'The training directory uses a link or junction. Resolve the data location before installing.'
  else if TrainingPathsOverlap(InstallPath, DataPath) then
    Result := 'Application files must stay separate from Xiayuluo configuration and training data. Choose another installation folder.';
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  InstallPath, DataPath: String;
begin
  Result := '';
  InstallPath := WizardDirValue;
  if not TrainingAbsolutePath(InstallPath) then
  begin
    Result := 'Choose an absolute installation folder.';
    Exit;
  end;
  if TrainingHasReparseParent(InstallPath) then
  begin
    Result := 'The installation folder must not use symbolic links or junctions.';
    Exit;
  end;
  if (TrainingGetFileAttributes(AddBackslash(InstallPath) + 'training') <> $FFFFFFFF) or
    (TrainingGetFileAttributes(AddBackslash(InstallPath) + '.vision-learning') <> $FFFFFFFF) or
    (TrainingGetFileAttributes(AddBackslash(InstallPath) + '.training-store.json') <> $FFFFFFFF) then
  begin
    Result := 'This installation folder contains training data. Keep the original data and choose a separate application folder.';
    Exit;
  end;
  Result := TrainingProtectedLocationError(InstallPath, ExpandConstant('{localappdata}\Xiayuluo'));
  if Result <> '' then
    Exit;
  Result := TrainingProtectedLocationError(InstallPath, ExpandConstant('{localappdata}\Xiayuluo\training'));
  if Result <> '' then
    Exit;
  if RegQueryStringValue(HKCU, 'Software\Xiayuluo', 'TrainingDataDir', DataPath) then
  begin
    Result := TrainingProtectedLocationError(InstallPath, DataPath);
    if Result <> '' then
      Exit;
  end;
  DataPath := GetEnv('ASSISTANT_TRAINING_DIR');
  Result := TrainingProtectedLocationError(InstallPath, DataPath);
end;

function DefaultDirectory(Param: String): String;
begin
  if DirExists('E:\') then
    Result := 'E:\Xiayuluo'
  else
    Result := ExpandConstant('{localappdata}\Programs\Xiayuluo');
end;
