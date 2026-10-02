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
