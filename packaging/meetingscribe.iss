; MeetingScribe installer - Inno Setup script.
;
; Deliberately lightweight: ships only source files, a minimal PySide6
; install (~50-80 MB), and a tiny native launcher exe. The heavy ML
; dependencies (torch, pyannote.audio, faster-whisper - multiple GB) are NOT
; bundled here; the app installs them itself on first launch, with its own
; GUI progress dialog (see gui/bootstrap.py), the same way setup.bat used to
; for the CLI version.
;
; All Python dependencies - PySide6 here, and everything gui/bootstrap.py
; installs later - go into a dedicated venv at {app}\venv, never the
; system/user Python. This is what makes uninstall clean: deleting {app}
; (see [UninstallDelete] below) removes every package this app ever
; installed, with zero risk of breaking some other, unrelated Python project
; on the same machine that happens to share a system-wide install.
;
; The launcher (launcher/launcher.py, built separately via PyInstaller - see
; README.md in this folder) is a real, tiny, dependency-free .exe whose only
; job is to spawn the venv's own pythonw.exe on run_gui.py, with zero console
; window. It exists so the Start Menu/Desktop shortcut is a genuine .exe with
; no terminal flash - it does not itself contain the app.
;
; Build: see README.md in this folder.
; Output: packaging\dist\MeetingScribe-Setup-<version>.exe

#define MyAppName "MeetingScribe"
#define MyAppVersion "2.0.0"
#define MyAppPublisher "Serhii Myshko"
#define MyAppURL "https://github.com/sergeiown/MeetingScribe"
#define MyAppExeName "MeetingScribe.exe"
#define SourceRoot "..\"

[Setup]
AppId={{4FF20F0C-F9B6-418C-88D1-EC2EE5F91221}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Per-user install, no admin prompt - matches the app's own per-user data
; layout (input/output/models/speakers/logs live next to the install dir).
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=MeetingScribe-Setup-{#MyAppVersion}
SetupIconFile={#SourceRoot}img\icon.ico
UninstallDisplayIcon={app}\img\icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
Source: "launcher\dist\MeetingScribe.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}core\*"; DestDir: "{app}\core"; Flags: recursesubdirs ignoreversion; Excludes: "__pycache__\*,*.pyc"
Source: "{#SourceRoot}gui\*"; DestDir: "{app}\gui"; Flags: recursesubdirs ignoreversion; Excludes: "__pycache__\*,*.pyc"
Source: "{#SourceRoot}samples\*"; DestDir: "{app}\samples"; Flags: recursesubdirs ignoreversion
Source: "{#SourceRoot}img\icon.ico"; DestDir: "{app}\img"; Flags: ignoreversion
Source: "{#SourceRoot}run_gui.py"; DestDir: "{app}"; Flags: ignoreversion
; run_gui.bat ships too, as a console-visible fallback for troubleshooting -
; it is not the default shortcut target (MeetingScribe.exe is).
Source: "{#SourceRoot}run_gui.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}config.env.example"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\img\icon.ico"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"; IconFilename: "{app}\img\icon.ico"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\img\icon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
; The venv (PySide6, plus everything gui/bootstrap.py installs into it on
; first launch - torch, pyannote.audio, faster-whisper, ...) isn't tracked
; by [Files] since it's created at runtime, not copied by the installer -
; this ensures the uninstaller removes it anyway. This is the
; "dependencies" half of the uninstall question asked in [Code] below;
; the "my data" half (models/input/output/speakers/logs, and {app} itself)
; is handled there instead, since whether to remove it is the user's
; choice, not a fixed list.
Type: filesandordirs; Name: "{app}\venv"

[Code]
var
  WipeDataOnUninstall: Boolean;

function InitializeUninstall(): Boolean;
begin
  Result := True;
  // A silent/unattended uninstall (e.g. a scripted reinstall) must not
  // wipe the user's speakers/transcripts without them ever seeing this
  // question - default to keeping data in that case, only the venv (see
  // [UninstallDelete]) goes regardless. An interactive uninstall always
  // asks, since which choice is "safe" depends on what the person wants.
  if UninstallSilent() then
    WipeDataOnUninstall := False
  else
    WipeDataOnUninstall := (MsgBox(
      'Also delete downloaded models, enrolled speakers, and saved transcripts?' + #13#10#13#10 +
      'Choose Yes for a full, clean removal (recommended if you''re done with MeetingScribe). ' +
      'Choose No to keep your data in place - useful if you plan to reinstall later.',
      mbConfirmation, MB_YESNO) = IDYES);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  // Runs after Inno's own removal (the venv above, plus every [Files]
  // entry) is already done, so by now {app} - if anything is left at all -
  // holds only the untracked runtime dirs (models/input/output/speakers/
  // logs/samples). Wiping it at that point removes exactly the "my data"
  // half, and {app} itself along with it, without touching anything the
  // standard uninstall step is still in the middle of handling.
  if (CurUninstallStep = usPostUninstall) and WipeDataOnUninstall then
    DelTree(ExpandConstant('{app}'), True, True, True);
end;

function IsPythonInstalled(): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec('cmd.exe', '/c python --version >nul 2>&1', '', SW_HIDE, ewWaitUntilTerminated, ResultCode)
    and (ResultCode = 0);
end;

procedure InstallPythonIfMissing();
var
  ResultCode: Integer;
begin
  if not IsPythonInstalled() then
  begin
    if MsgBox('Python was not found on this system. MeetingScribe needs Python 3.12 or newer.' + #13#10#13#10 +
              'Install it now via winget? This needs an internet connection.',
              mbConfirmation, MB_YESNO) = IDYES then
    begin
      WizardForm.StatusLabel.Caption := 'Installing Python (this may take a few minutes)...';
      WizardForm.Update;
      Exec('cmd.exe',
        '/c winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements',
        '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
      if not IsPythonInstalled() then
        MsgBox('Python installation could not be confirmed. If winget succeeded, ' +
               'you may need to log out and back in for PATH changes to apply, ' +
               'then launch MeetingScribe again.', mbInformation, MB_OK);
    end
    else
      MsgBox('Install Python 3.12+ from https://python.org, then launch MeetingScribe again.',
             mbInformation, MB_OK);
  end;
end;

function VenvPythonPath(): String;
begin
  Result := ExpandConstant('{app}\venv\Scripts\python.exe');
end;

procedure SetupVenvAndPySide6();
var
  ResultCode: Integer;
begin
  // A dedicated venv, never the system/user Python - see the header comment
  // for why. No console window, ever - WizardForm.StatusLabel is the only
  // feedback shown while this runs.
  if not FileExists(VenvPythonPath()) then
    Exec('cmd.exe', '/c python -m venv "' + ExpandConstant('{app}\venv') + '"',
      '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  if FileExists(VenvPythonPath()) then
    Exec('cmd.exe', '/c "' + VenvPythonPath() + '" -m pip install -q PySide6',
      '', SW_HIDE, ewWaitUntilTerminated, ResultCode)
  else
    MsgBox('Could not create the private Python environment MeetingScribe needs. ' +
           'Try launching MeetingScribe again, or reinstall it.', mbInformation, MB_OK);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    WizardForm.StatusLabel.Caption := 'Checking Python installation...';
    WizardForm.Update;
    InstallPythonIfMissing();
    if IsPythonInstalled() then
    begin
      WizardForm.StatusLabel.Caption := 'Setting up a private Python environment for MeetingScribe (this may take a minute)...';
      WizardForm.Update;
      SetupVenvAndPySide6();
    end;
    WizardForm.StatusLabel.Caption := 'Finishing up...';
    WizardForm.Update;
  end;
end;
