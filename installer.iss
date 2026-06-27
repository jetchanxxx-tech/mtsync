; ============================================================
; MT Copy Trading System — Inno Setup Installer Script
;
; Prerequisites:
;   1. Install Inno Setup 6: https://jrsoftware.org/isinfo.php
;   2. Run build.bat first to generate dist\MT_Copy_Trading.exe
;   3. Open this file in Inno Setup Compiler → Build → Compile
; ============================================================

#define MyAppName "MT Copy Trading System"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "CopyTech"
#define MyAppURL "https://example.com"
#define MyAppExeName "MT_Copy_Trading.exe"

[Setup]
AppId={{B8F4A3D2-1E5C-4A7B-9D3F-6C8E2A1B4F7D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\MT_Copy_Trading
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=LICENSE.txt
OutputDir=installer_output
OutputBaseFilename=MT_Copy_Trading_Setup_v{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "Create &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\.env.template"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch MT Copy Trading System"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: files; Name: "{app}\*.log"
Type: files; Name: "{app}\*.json"

[Code]
function InitializeSetup: Boolean;
begin
  Result := True;
end;
