; One downloadable installer; user data lives outside the installation directory.
#ifndef ProjectRoot
  #error ProjectRoot is required
#endif
[Setup]
AppId=InvoiceAssistant.Local
AppName=Invoice Assistant
AppVersion=0.1.0
AppPublisher=Invoice Assistant
AppPublisherURL=https://github.com/rpg05832/invoice-assistant
DefaultDirName={localappdata}\Programs\InvoiceAssistant
DefaultGroupName=Invoice Assistant
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#ProjectRoot}\dist
OutputBaseFilename=InvoiceAssistant-Setup-0.1.0-x64
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\InvoiceAssistant.exe
CloseApplications=yes
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
#ifdef HebrewLanguageFile
Name: "hebrew"; MessagesFile: "{#HebrewLanguageFile}"
#endif

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "{#ProjectRoot}\dist\InvoiceAssistant\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\Invoice Assistant"; Filename: "{app}\InvoiceAssistant.exe"; WorkingDir: "{app}"
Name: "{userdesktop}\Invoice Assistant"; Filename: "{app}\InvoiceAssistant.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\InvoiceAssistant.exe"; Description: "Open Invoice Assistant"; Flags: nowait postinstall skipifsilent
