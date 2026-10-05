; InvoiceFlow Windows Installer

#define MyAppName "InvoiceFlow"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "InvoiceFlow"
#define MyAppExeName "InvoiceFlow.exe"

[Setup]
AppId={{8B9A6E7C-1F41-4D53-9B8C-INVFLOW100}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\InvoiceFlow
DefaultGroupName={#MyAppName}
OutputDir=installer
OutputBaseFilename=InvoiceFlow_Setup_v{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
Uninstallable=yes
ArchitecturesAllowed=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "dist\InvoiceFlow\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\InvoiceFlow"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\InvoiceFlow"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch InvoiceFlow"; Flags: nowait postinstall skipifsilent