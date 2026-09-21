"""Tiny native launcher for MeetingScribe.

Frozen alone via PyInstaller (a few MB); deliberately imports none of the
app's own heavy dependencies (PySide6, torch, ...). Its only job: find
pythonw.exe and spawn run_gui.py next to it with zero console window, then
exit. It exists purely so the Start Menu/Desktop shortcut is a real .exe
with no terminal flash - the app itself still ships as plain source.
"""

import ctypes
import shutil
import subprocess
import sys
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000
MB_ICONERROR = 0x10


def _find_pythonw(app_dir: Path) -> str:
    # The venv's pythonw.exe always takes priority; the system-wide lookup
    # below is only a defensive fallback if venv creation ever failed.
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
    # No PySide6 available here (this launcher deliberately has no
    # dependencies) - a raw Win32 message box needs none either, so a spawn
    # failure isn't silently swallowed.
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
