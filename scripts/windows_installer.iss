; One downloadable installer; user data lives outside the installation directory.
#ifndef ProjectRoot
  #error ProjectRoot is required
#endif
[Setup]
AppId=InvoiceAssistant.Local
AppName=Invoice Assistant
AppVersion=0.2.0
AppPublisher=Invoice Assistant
AppPublisherURL=https://github.com/rpg05832/invoice-assistant
DefaultDirName={localappdata}\Programs\InvoiceAssistant
DefaultGroupName=Invoice Assistant
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#ProjectRoot}\dist
OutputBaseFilename=InvoiceAssistant-Setup-0.2.0-x64
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
Source: "{#ProjectRoot}\build\webview\MicrosoftEdgeWebView2RuntimeInstallerX64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: NeedsWebView2
Source: "{#ProjectRoot}\dist\InvoiceAssistant\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\Invoice Assistant"; Filename: "{app}\InvoiceAssistant.exe"; WorkingDir: "{app}"
Name: "{userdesktop}\Invoice Assistant"; Filename: "{app}\InvoiceAssistant.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{tmp}\MicrosoftEdgeWebView2RuntimeInstallerX64.exe"; Parameters: "/silent /install"; StatusMsg: "Installing Microsoft WebView2 for the application window..."; Flags: waituntilterminated; Check: NeedsWebView2; AfterInstall: VerifyWebView2
Filename: "{app}\InvoiceAssistant.exe"; Description: "Open Invoice Assistant"; Flags: nowait postinstall skipifsilent

[Code]
function NeedsWebView2: Boolean;
var Version: String;
begin
  Result := True;
  if RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) then
    if (StrToIntDef(Copy(Version, 1, Pos('.', Version) - 1), 0) >= 120) then Result := False;
  if RegQueryStringValue(HKLM32, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) then
    if (StrToIntDef(Copy(Version, 1, Pos('.', Version) - 1), 0) >= 120) then Result := False;
end;

procedure VerifyWebView2;
begin
  if NeedsWebView2 then
    RaiseException('Microsoft WebView2 could not be installed. See the installation log.');
end;
