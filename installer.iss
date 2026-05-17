; ============================================
; Inno Setup Script für KI-Transcribe Pro
; ============================================
; Erstellt einen professionellen Windows-Installer
; KEINE Admin-Rechte erforderlich (User-Installation)
; ============================================

#define MyAppName "KI-Transcribe Pro"
#define MyAppVersion "2.0"
#define MyAppPublisher "KI-Transcribe Team"
#define MyAppExeName "KI-Transcribe-Pro.exe"
#define MyAppDescription "Medizinisches Transkriptions- und Diktiersystem"

[Setup]
; ============================================
; BASIC SETUP
; ============================================
AppId={{8B3E9F2A-7D4C-4E1B-9F6A-2C8D5E4F3A1B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL=https://github.com/yourusername/ki-transcribe
AppSupportURL=https://github.com/yourusername/ki-transcribe/issues
AppUpdatesURL=https://github.com/yourusername/ki-transcribe/releases
DefaultDirName={localappdata}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=Output
OutputBaseFilename=KI-Transcribe-Installer
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=
UninstallDisplayIcon={app}\{#MyAppExeName}

; ============================================
; KEINE ADMIN-RECHTE ERFORDERLICH!
; ============================================
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; ============================================
; ARCHITEKTUR & KOMPATIBILITÄT
; ============================================
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763

; ============================================
; LIZENZ & INFORMATIONEN
; ============================================
LicenseFile=
InfoBeforeFile=
InfoAfterFile=
DisableWelcomePage=no
DisableProgramGroupPage=no

[Languages]
Name: "german"; MessagesFile: "compiler:Languages\German.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "quicklaunchicon"; Description: "Quick Launch-Symbol erstellen"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Alle Dateien aus dem PyInstaller-Output
Source: "dist\KI-Transcribe-Pro\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Start-Menü
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Comment: "{#MyAppDescription}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"

; Desktop-Icon (optional)
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; Comment: "{#MyAppDescription}"

; Quick Launch (optional)
Name: "{userappdata}\Microsoft\Internet Explorer\Quick Launch\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: quicklaunchicon

[Run]
; Nach Installation: App starten (optional)
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
var
  ConfigDirCreated: Boolean;

procedure InitializeWizard();
begin
  ConfigDirCreated := False;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ConfigDir: String;
begin
  if CurStep = ssPostInstall then
  begin
    // Erstelle Config-Verzeichnis in AppData
    ConfigDir := ExpandConstant('{userappdata}\KI-Transcribe-Pro');
    if not DirExists(ConfigDir) then
    begin
      CreateDir(ConfigDir);
      ConfigDirCreated := True;
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ConfigDir: String;
  MsgResult: Integer;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    ConfigDir := ExpandConstant('{userappdata}\KI-Transcribe-Pro');

    if DirExists(ConfigDir) then
    begin
      MsgResult := MsgBox(
        'Möchten Sie auch Ihre Konfiguration und gespeicherten Transkripte löschen?' + #13#10 + #13#10 + 'Verzeichnis: ' + ConfigDir,
        mbConfirmation,
        MB_YESNO
      );

      if MsgResult = IDYES then
      begin
        DelTree(ConfigDir, True, True, True);
      end;
    end;
  end;
end;

[Messages]
; Deutsche Meldungen
WelcomeLabel2=Dies wird [name/ver] auf Ihrem Computer installieren.%n%nKeine Administrator-Rechte erforderlich!
FinishedHeadingLabel=Installation von [name] abgeschlossen
FinishedLabel=[name] wurde erfolgreich installiert.%n%nBeim ersten Start werden Sie nach der Server-URL gefragt.

[CustomMessages]
german.AdditionalIcons=Zusätzliche Symbole:
german.CreateDesktopIcon=Desktop-Symbol erstellen
german.CreateQuickLaunchIcon=Quick Launch-Symbol erstellen
german.LaunchProgram=%1 starten
german.UninstallProgram=%1 deinstallieren
