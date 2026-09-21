; MeetingScribe installer - Inno Setup script.
;
; Ships only source files and a minimal PySide6 install. Heavy ML
; dependencies (torch, pyannote.audio, faster-whisper) are installed by the
; app itself on first launch (see gui/bootstrap.py).
;
; All Python dependencies go into a dedicated venv at {app}\venv, never the
; system/user Python, so uninstall (see [UninstallDelete]) can remove
; everything cleanly without touching any other Python install.
;
; System Python and ffmpeg are installed via winget if missing (see [Code]) -
; both are real prerequisites the app itself can't bundle.
;
; Shortcuts launch the venv's own pythonw.exe directly on run_gui.py (no
; console window - that's what the "w" in pythonw is for) instead of a
; custom-built launcher exe. A prior PyInstaller-frozen launcher.exe was
; tried and dropped: being a brand-new, unsigned, never-seen binary, it got
; silently quarantined by Windows Defender's cloud/heuristic scanning some
; time after install on more than one machine, breaking the Start Menu/
; desktop shortcuts. pythonw.exe is Microsoft-signed and already trusted,
; so it isn't subject to that risk.
;
; Build: see BUILDING.md in this folder.
; Output: packaging\dist\MeetingScribe-Setup-<version>.exe

#define MyAppName "MeetingScribe"
#define MyAppVersion "2.0.0"
#define MyAppPublisher "Serhii Myshko"
#define MyAppURL "https://github.com/sergeiown/MeetingScribe"
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
; Both explicit for the in-app updater's /SILENT run (see [Run] below).
; RestartApplications=no avoids racing the postinstall relaunch there -
; Inno 6 defaults it to "yes", which would risk launching MeetingScribe
; twice after every update.
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
Source: "{#SourceRoot}core\*"; DestDir: "{app}\core"; Flags: recursesubdirs ignoreversion; Excludes: "__pycache__\*,*.pyc"
Source: "{#SourceRoot}gui\*"; DestDir: "{app}\gui"; Flags: recursesubdirs ignoreversion; Excludes: "__pycache__\*,*.pyc"
Source: "{#SourceRoot}samples\*"; DestDir: "{app}\samples"; Flags: recursesubdirs ignoreversion
Source: "{#SourceRoot}img\icon.ico"; DestDir: "{app}\img"; Flags: ignoreversion
Source: "{#SourceRoot}run_gui.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}run_gui.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}config.env.example"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\venv\Scripts\pythonw.exe"; Parameters: """{app}\run_gui.py"""; WorkingDir: "{app}"; IconFilename: "{app}\img\icon.ico"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"; IconFilename: "{app}\img\icon.ico"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\venv\Scripts\pythonw.exe"; Parameters: """{app}\run_gui.py"""; WorkingDir: "{app}"; IconFilename: "{app}\img\icon.ico"; Tasks: desktopicon

[Run]
; No skipifsilent: the in-app updater runs this with /SILENT and needs the
; app to relaunch itself afterward (see core.spawn_installer). A normal
; interactive install still shows this as an optional, user-visible checkbox.
Filename: "{app}\venv\Scripts\pythonw.exe"; Parameters: """{app}\run_gui.py"""; WorkingDir: "{app}"; Description: "Launch {#MyAppName}"; Flags: postinstall nowait

[UninstallDelete]
; The venv isn't tracked by [Files] since it's created at runtime, not
; copied by the installer - this ensures it's removed anyway. Data files
; (models/input/output/speakers/logs) are handled separately in [Code],
; since whether to remove them is the user's choice.
Type: filesandordirs; Name: "{app}\venv"
; Python's own bytecode cache, written at runtime under the source dirs
; Setup installed - not tracked by [Files], so it survives a "keep data"
; uninstall otherwise, along with the now near-empty core\/gui\ folders.
Type: filesandordirs; Name: "{app}\core\__pycache__"
Type: filesandordirs; Name: "{app}\gui\__pycache__"

[Code]
var
  WipeDataOnUninstall: Boolean;

function InitializeUninstall(): Boolean;
begin
  Result := True;
  // A silent uninstall must not wipe user data without asking - default to
  // keeping it (only the venv is removed regardless). Interactive always asks.
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
  // Runs after Inno's own removal is done, so {app} here holds only the
  // untracked data dirs - wiping it removes exactly the "my data" half.
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

function IsFfmpegInstalled(): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec('cmd.exe', '/c ffmpeg -version >nul 2>&1', '', SW_HIDE, ewWaitUntilTerminated, ResultCode)
    and (ResultCode = 0);
end;

procedure InstallFfmpegIfMissing();
var
  ResultCode: Integer;
begin
  if not IsFfmpegInstalled() then
  begin
    if MsgBox('ffmpeg was not found on this system. MeetingScribe needs it to read audio/video files.' + #13#10#13#10 +
              'Install it now via winget? This needs an internet connection.',
              mbConfirmation, MB_YESNO) = IDYES then
    begin
      WizardForm.StatusLabel.Caption := 'Installing ffmpeg (this may take a few minutes)...';
      WizardForm.Update;
      Exec('cmd.exe',
        '/c winget install -e --id Gyan.FFmpeg --silent --accept-package-agreements --accept-source-agreements',
        '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
      // find_ffmpeg() in core/audio.py also checks this default install
      // path directly, so a PATH refresh isn't required for the app itself
      // to find it - only for typing "ffmpeg" at a fresh command prompt.
      if not (IsFfmpegInstalled() or FileExists('C:\Program Files\ffmpeg\bin\ffmpeg.exe')) then
        MsgBox('ffmpeg installation could not be confirmed. If winget succeeded, ' +
               'MeetingScribe should still find it automatically - launch it and try ' +
               'a file. Otherwise install manually: https://www.gyan.dev/ffmpeg/builds/',
               mbInformation, MB_OK);
    end
    else
      MsgBox('Install ffmpeg from https://www.gyan.dev/ffmpeg/builds/, then launch MeetingScribe again.',
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
  // Dedicated venv, never system/user Python (see header). No console
  // window - StatusLabel is the only feedback shown while this runs.
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
    WizardForm.StatusLabel.Caption := 'Checking ffmpeg installation...';
    WizardForm.Update;
    InstallFfmpegIfMissing();
    WizardForm.StatusLabel.Caption := 'Finishing up...';
    WizardForm.Update;
  end;
end;
