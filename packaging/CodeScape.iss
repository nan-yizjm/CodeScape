#ifndef BuildVersion
  #define BuildVersion "0.1.0"
#endif
#ifndef BundleDir
  #define BundleDir "..\.runtime\packaging\dist\CodeScape"
#endif
#ifndef OutputDir
  #define OutputDir "..\output\releases"
#endif

[Setup]
AppId={{DEEF7805-92F7-4EAC-8171-67D647C72D35}
AppName=CodeScape 源境
AppVersion={#BuildVersion}
AppPublisher=CodeScape
DefaultDirName={localappdata}\Programs\CodeScape
DefaultGroupName=CodeScape
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=CodeScape-{#BuildVersion}-Windows-x64-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
CloseApplications=no
RestartApplications=no
UninstallDisplayIcon={app}\CodeScape.exe

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: unchecked

[Icons]
Name: "{group}\CodeScape 源境"; Filename: "{app}\CodeScape.exe"
Name: "{autodesktop}\CodeScape 源境"; Filename: "{app}\CodeScape.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\CodeScape.exe"; Description: "启动 CodeScape 源境"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 应用数据、DPAPI 密钥、用户原始源码由应用内管理；卸载程序不删除这些目录。

[Code]
function StopAndCheckApplication(): Boolean;
var
  ResultCode: Integer;
  Executable: String;
begin
  Executable := ExpandConstant('{app}\CodeScape.exe');
  Result := True;
  if FileExists(Executable) then
    Result := Exec(Executable, '--prepare-update --no-browser', '', SW_HIDE,
      ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if not StopAndCheckApplication() then
    Result := '应用尚未完成安全保存或数据校验。安装已停止，现有程序和学习数据保持不变。';
end;

function InitializeUninstall(): Boolean;
begin
  Result := StopAndCheckApplication();
  if not Result then
    MsgBox('应用尚未完成安全保存。卸载已停止，请稍后重试。', mbError, MB_OK);
end;
