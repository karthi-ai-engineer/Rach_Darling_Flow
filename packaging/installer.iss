; Inno Setup script for SST Dictation. build_installer.cmd compiles it after PyInstaller has
; produced dist\sst\ (the app, with the model in dist\sst\models\).
#ifndef AppVersion
  #error Pass /DAppVersion=x.y.z (build_installer.cmd does this)
#endif
#define AppName "SST Dictation"
#define AppExe "sst.exe"

[Setup]
AppId={{8F1C5A7E-3B2D-4E6A-9C1F-5D2E7A4B9C31}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=karthi-ai-engineer
AppPublisherURL=https://github.com/karthi-ai-engineer/Rach_Darling_Flow
AppSupportURL=https://github.com/karthi-ai-engineer/Rach_Darling_Flow/issues
; Per-user install into %LOCALAPPDATA%\Programs: no administrator rights needed.
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; sst_app.py holds this mutex while running, so setup and uninstall ask to close the app first.
AppMutex=SST-Dictation-running
OutputDir=..\dist
OutputBaseFilename=SST-Dictation-Setup-{#AppVersion}
SetupIconFile=sst.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
; The model is ~95% of the size; LZMA2 brings it to about 60%. Solid mode would only add memory use.
Compression=lzma2/normal
SolidCompression=no
LZMAUseSeparateProcess=yes
LZMANumBlockThreads=4

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked
Name: "startup"; Description: "Start dictation automatically when I sign in to Windows"; GroupDescription: "Startup:"; Flags: unchecked

[InstallDelete]
; Updating: remove the previous version's program files first, so no stale DLLs are left behind.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\sst\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; Comment: "Press Ctrl+Alt+D in any app and speak"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Comment: "Press Ctrl+Alt+D in any app and speak"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: startup; Flags: runminimized

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName} now"; Flags: nowait postinstall skipifsilent
