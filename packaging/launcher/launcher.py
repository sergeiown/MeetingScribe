"""Tiny native launcher for MeetingScribe.

Frozen alone via PyInstaller (a few MB) - deliberately does not import any
of the app's own heavy dependencies (PySide6, torch, ...). Its only job:
find pythonw.exe and spawn run_gui.py next to it with zero console window,
then exit immediately. The actual app (core/, gui/, run_gui.py) ships as
plain source, installed by the same Inno Setup installer alongside this
launcher - this exe exists purely so the Start Menu/Desktop shortcut is a
real .exe with no terminal flash, not so the app itself is "compiled".
"""

import ctypes
import shutil
import subprocess
import sys
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000
MB_ICONERROR = 0x10


def _find_pythonw(app_dir: Path) -> str:
    # The installer creates a dedicated venv at {app}\venv and installs
    # everything there, never into the system/user Python - this is what
    # lets uninstall cleanly remove every dependency (see meetingscribe.iss).
    # Its pythonw.exe always takes priority; a system-wide lookup is only a
    # defensive fallback (e.g. if venv creation ever failed).
    venv_pythonw = app_dir / "venv" / "Scripts" / "pythonw.exe"
    if venv_pythonw.exists():
        return str(venv_pythonw)
    python = shutil.which("python") or shutil.which("python3")
    if python:
        candidate = Path(python).with_name("pythonw.exe")
        if candidate.exists():
            return str(candidate)
    return "pythonw"  # fall back to a plain PATH lookup


def _show_error(message: str) -> None:
    # No PySide6 available yet at this point (this launcher deliberately
    # doesn't depend on it) - a raw Win32 message box is the only UI that
    # needs zero dependencies, so a spawn failure isn't silently swallowed.
    ctypes.windll.user32.MessageBoxW(0, message, "MeetingScribe", MB_ICONERROR)


def main():
    app_dir = Path(sys.executable).resolve().parent
    run_gui = app_dir / "run_gui.py"
    try:
        subprocess.Popen(
            [_find_pythonw(app_dir), str(run_gui)],
            cwd=str(app_dir),
            creationflags=CREATE_NO_WINDOW,
        )
    except OSError as e:
        _show_error(
            f"Could not start MeetingScribe: {e}\n\n"
            "If Python was just installed, log out and back in (or restart "
            "your PC), then try again.")


if __name__ == "__main__":
    main()
