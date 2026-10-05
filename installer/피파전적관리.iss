; 설치 파일(setup.exe) — Inno Setup 6. tools/release.py 가 /DAppVersion=X.Y.Z 로 부른다.
; 입력은 PyInstaller onedir 결과(dist\피파전적관리\) 그대로다.
; 이 파일은 UTF-8 BOM 으로 둔다 — BOM 이 없으면 컴파일러가 한글을 ANSI 로 읽는다.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppExe "피파전적관리.exe"
; 화면 이름 — config.APP_NAME 과 같게. 폴더·exe·AppId 는 예전 이름 그대로(바꾸면 업데이트가 끊긴다)
#define AppTitle "감독모드 전적 분석"
#define OldTitle "피파 전적관리"
; 앱 쪽 상수와 같아야 한다(test_release 가 대조) — autostart.VALUE_INSTALLED
#define RunValue "FifaMatchTracker"
; tray.QUIT_ARG 와 같아야 한다(test_release 가 대조)
#define QuitArg "--quit"

[Setup]
; AppId 는 바꾸지 않는다 — 바뀌면 새 버전이 덮어쓰기가 아니라 별개 프로그램으로 깔린다
AppId={{FD0C45E7-7115-44EB-9318-CB5CD8B8CF69}
AppName={#AppTitle}
AppVersion={#AppVersion}
AppVerName={#AppTitle} v{#AppVersion}
AppPublisher=yim2412
AppPublisherURL=https://github.com/yim2412/fifa-match-tracker
AppSupportURL=https://github.com/yim2412/fifa-match-tracker/releases
; 사용자 폴더에 깐다(%LOCALAPPDATA%\Programs) — 관리자 권한 창이 안 뜬다
PrivilegesRequired=lowest
DefaultDirName={autopf}\피파전적관리
DisableProgramGroupPage=yes
OutputDir=..\dist
; GitHub 는 첨부 파일 이름의 한글을 지운다(v0.2.0 zip 이 '-v0.2.0.zip' 이 됐다) — 그래서 영문.
; release.py 의 ASSET_PREFIX 와 같아야 한다
OutputBaseFilename=FifaMatchTracker-Setup-v{#AppVersion}
SetupIconFile=..\app_icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppTitle}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; 켜 둔 채 업데이트하면 파일이 잠겨 있다 — 닫을지 묻는다
CloseApplications=yes

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; 덮어 설치할 때 옛 버전의 모듈이 남아 섞이지 않게 — 데이터는 %LOCALAPPDATA%\피파전적관리 라 여기와 무관하다
Type: filesandordirs; Name: "{app}\_internal"
; v0.3.0 까지의 바로가기 이름 — 이름이 바뀌어 덮어쓰지 않으니 지운다
Type: files; Name: "{autoprograms}\{#OldTitle}.lnk"
Type: files; Name: "{autodesktop}\{#OldTitle}.lnk"

[Files]
Source: "..\dist\피파전적관리\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppTitle}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppTitle}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppTitle}}"; Flags: nowait postinstall skipifsilent
; 앱 안 [업데이트] 로 실행됐을 때(/AUTOUPDATE=1, /SILENT)는 끝나면 앱을 다시 켠다 — updatecheck.INSTALL_ARGS
Filename: "{app}\{#AppExe}"; Flags: nowait; Check: IsAutoUpdate

[Code]
function IsAutoUpdate: Boolean;
begin
  Result := ExpandConstant('{param:AUTOUPDATE|0}') = '1';
end;

// 제거할 때 자동 실행 등록을 지운다 — 앱 토글(autostart.py)이 만든 값이라 [Registry] 로는 못 지운다.
// 설치판 값만(autostart.VALUE_INSTALLED) — 포터블 값은 포터블 몫이다. 값이 없어도 그냥 넘어간다.
// 떠 있는 앱(트레이 상주 포함)을 먼저 끝낸다 — 제거기는 설치기와 달리 떠 있는 앱을 닫지 않아, 앱이 계속 돌고
// 설치 폴더가 통째로 남았다(1.1.1 실측). --quit 은 떠 있는 실행본에 종료를 부탁하고 끝날 때까지 기다린다(tray.request_quit).
// 떠 있지 않으면 아무것도 켜지 않는다. 확인 창 뒤(usUninstall)라 취소하면 앱은 그대로다.
// 끝난 앱의 뮤텍스는 프로세스가 완전히 내려가기 직전에 풀려, 파일은 다 지워져도 빈 폴더가 남았다(실측) —
// 끝에서 빈 폴더 지우기를 잠깐 다시 시도한다. RemoveDir 은 빈 폴더만 지운다(사용자가 넣은 파일이 있으면 그대로).
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode, I: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    Exec(ExpandConstant('{app}\{#AppExe}'), '{#QuitArg}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    RegDeleteValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', '{#RunValue}');
  end;
  if CurUninstallStep = usPostUninstall then
    for I := 1 to 20 do
    begin
      if not DirExists(ExpandConstant('{app}')) then
        Break;
      RemoveDir(ExpandConstant('{app}'));
      Sleep(250);
    end;
end;
