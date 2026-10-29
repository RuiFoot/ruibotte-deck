; Inno Setup: RuiBotte Deck 설치 프로그램 (사용자별 설치 — 관리자 권한 불필요, '설정 > 앱'에서 제거 가능)
; 빌드: packaging/build.ps1 (먼저 PyInstaller로 dist\RuiBotteDeck 를 만든 뒤 ISCC로 이 파일을 컴파일)
#define AppName "RuiBotte Deck"
; 버전은 build.ps1 이 desktop.py 의 APP_VERSION 을 /DAppVersion= 으로 넘김
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppExe "RuiBotteDeck.exe"

[Setup]
AppId={{6F1C2B9E-5D4A-4E8B-9C3F-DE5C0A7B2F41}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=RuiBotte
AppComments=PC status and AI coding usage dashboard
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=RuiBotteDeck-Setup-{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "autostart"; Description: "Windows에 로그인할 때 자동 실행"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\RuiBotteDeck\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: autostart

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
; 앱 안 업데이트(/SILENT /relaunch=1)로 설치했으면 끝난 뒤 다시 실행
Filename: "{app}\{#AppExe}"; Flags: nowait; Check: RelaunchRequested

[UninstallRun]
; 실행 중이면 먼저 종료 (트레이 앱 + 대시보드 전용 브라우저 창)
Filename: "{sys}\taskkill.exe"; Parameters: "/F /T /IM {#AppExe}"; Flags: runhidden; RunOnceId: "KillApp"

[UninstallDelete]
; 앱이 직접 켠 자동 실행 바로가기도 정리. 기록(DB)은 %LOCALAPPDATA%\PCDashboard 에 남김 (다시 설치하면 이어서 사용)
Type: files; Name: "{userstartup}\{#AppName}.lnk"

[Code]
function RelaunchRequested: Boolean;
begin
  Result := WizardSilent and (ExpandConstant('{param:relaunch|0}') = '1');
end;

// 업데이트 설치 전: 실행 중인 앱(트레이 + 대시보드 창)을 먼저 끝냄 — 안 끝나면 파일이 바뀌지 않은 채 '설치 완료'가 됨
function PrepareToInstall(var NeedsRestart: Boolean): String;
var Code: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM {#AppExe}', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Sleep(1000);
  Result := '';
end;
