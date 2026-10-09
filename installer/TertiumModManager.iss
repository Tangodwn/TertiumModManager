#ifndef MyAppVersion
  #error MyAppVersion must be supplied by the release builder (ISCC /DMyAppVersion=x.y.z)
#endif

#define MyAppName "Tertium Mod Manager"
#define MyAppPublisher "Tertium Mod Manager Community Project"
#define MyAppExeName "TertiumModManager.exe"

[Setup]
AppId={{6C7707EF-68AC-4C2F-A873-BB3F3CF75AF9}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\TertiumModManager
DefaultGroupName=Tertium Mod Manager
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\release
OutputBaseFilename=TertiumModManager-Setup-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\assets\tertium.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "nxmhandler"; Description: "Use Tertium for Nexus Mod Manager Download links (nxm://)"; GroupDescription: "Nexus integration:"; Flags: checkedonce

[Files]
Source: "..\dist\TertiumModManager\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Tertium Mod Manager"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\Tertium Mod Manager"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Classes\nxm"; ValueType: string; ValueName: ""; ValueData: "URL:Nexus Mods Protocol"; Flags: uninsdeletekey; Tasks: nxmhandler
Root: HKCU; Subkey: "Software\Classes\nxm"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""; Tasks: nxmhandler
Root: HKCU; Subkey: "Software\Classes\nxm\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"; Tasks: nxmhandler
Root: HKCU; Subkey: "Software\Classes\nxm\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: nxmhandler

; No [Run] auto-launch.
; Manual/bootstrap installs finish cleanly and the user launches from the shortcut.
; In-app self-update restarts Tertium after the silent installer exits.
