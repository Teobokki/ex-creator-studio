#define MyVer "1.3.0"
[Setup]
AppId={{C3E7A1D4-5B2F-4E8A-9C61-0A1B2C3D4E5F}
AppName=EX CREATOR STUDIO
AppVersion={#MyVer}
AppVerName=EX CREATOR STUDIO {#MyVer}
AppPublisher=EX Inc./HYPERION DIGITAL
AppPublisherURL=https://github.com/Teobokki/ex-creator-studio
AppCopyright=©2026 EX Inc./HYPERION DIGITAL ©2026
VersionInfoVersion={#MyVer}.0
VersionInfoCompany=EX Inc./HYPERION DIGITAL
VersionInfoCopyright=©2026 EX Inc./HYPERION DIGITAL ©2026
VersionInfoProductName=EX CREATOR STUDIO
VersionInfoDescription=EX CREATOR STUDIO Setup
DefaultDirName={localappdata}\Programs\EX CREATOR STUDIO
DefaultGroupName=EX CREATOR STUDIO
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=EX.CREATOR.STUDIO.Setup.{#MyVer}
SetupIconFile=assets\logo.ico
UninstallDisplayIcon={app}\EX CREATOR STUDIO.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "dist\EX CREATOR STUDIO\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\EX CREATOR STUDIO"; Filename: "{app}\EX CREATOR STUDIO.exe"
Name: "{group}\Uninstall EX CREATOR STUDIO"; Filename: "{uninstallexe}"
Name: "{userdesktop}\EX CREATOR STUDIO"; Filename: "{app}\EX CREATOR STUDIO.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\EX CREATOR STUDIO.exe"; Description: "Launch EX CREATOR STUDIO now"; Flags: nowait postinstall skipifsilent
