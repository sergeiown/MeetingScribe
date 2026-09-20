# Building the installer

Two steps, in order - the launcher exe must exist before the installer script
runs, since it gets bundled into it.

## 1. Build the launcher exe

```bat
cd launcher
python -m PyInstaller --onefile --noconsole --icon=..\..\img\icon.ico --name=MeetingScribe launcher.py
```

Produces `launcher\dist\MeetingScribe.exe` - a small (~8 MB), dependency-free
executable whose only job is to spawn `pythonw run_gui.py` with no console
window. It does not contain the app itself (see `launcher/launcher.py`'s
docstring).

## 2. Build the installer

```bat
"C:\Users\<you>\AppData\Local\Programs\Inno Setup 6\ISCC.exe" meetingscribe.iss
```

Produces `dist\MeetingScribe-Setup-<version>.exe`. See `meetingscribe.iss`'s
header comment for what it does and does not bundle (deliberately
lightweight - the heavy ML dependencies install on first launch, not here).

## Before publishing anywhere

Per this project's release process: build locally, test the install/upgrade
path yourself first, get an explicit go-ahead - only then tag/publish. Never
skip straight from a successful build to a release.
