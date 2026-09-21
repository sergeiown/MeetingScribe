#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GUI entry point. The installed app's shortcuts run this directly via
the venv's pythonw.exe, with no console window."""

import logging
import os
import sys
from pathlib import Path

# Under pythonw.exe (no console attached), sys.stdout/sys.stderr are None -
# not just redirected, literally None. Any code that prints, or an uncaught
# exception's own default traceback-printing, then crashes a SECOND time
# calling None.write(...) - which is how the whole app can silently vanish
# with zero trace anywhere. Must be fixed before anything else can run.
if sys.stdout is None or sys.stderr is None:
    _logs_dir = Path(__file__).resolve().parent / "logs"
    _logs_dir.mkdir(parents=True, exist_ok=True)
    _stderr_log = open(_logs_dir / "stderr.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or _stderr_log
    sys.stderr = sys.stderr or _stderr_log


def _excepthook(exc_type, exc_value, exc_tb):
    import traceback
    traceback.print_exception(exc_type, exc_value, exc_tb, file=sys.stderr)
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        if QApplication.instance() is not None:
            QMessageBox.critical(
                None, "MeetingScribe - unexpected error",
                f"{exc_type.__name__}: {exc_value}\n\n"
                "Details were written to logs\\stderr.log.")
    except Exception:
        pass


sys.excepthook = _excepthook

# pyannote.audio pulls in sympy, which pulls in matplotlib.pyplot; matplotlib
# auto-selects a Qt-integrated GUI backend when it detects PySide6 already
# loaded in the process, and that second, independent Qt-event-loop
# integration conflicts with our own, crashing Qt6Core.dll right after a
# transcription finishes (Windows Event ID 1000, always the same faulting
# offset - a deterministic code path, not a random race). Must be set before
# matplotlib is imported anywhere, i.e. before pyannote.audio is imported.
os.environ.setdefault("MPLBACKEND", "Agg")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# torch on Windows has no triton; silence its W-level log spam (flop_counter etc.)
os.environ.setdefault("TORCH_CPP_LOG_LEVEL", "ERROR")
os.environ.setdefault("HF_HUB_VERBOSITY", "error")  # hide HF Hub's "unauthenticated requests" notice
logging.getLogger("torch").setLevel(logging.ERROR)

# PySide6/Qt and torch/ctranslate2 can each ship their own OpenMP runtime DLL;
# having both loaded in the same process has been observed to crash (Windows
# Event ID 1000, faulting module Qt6Core.dll) after a transcription finishes
# and the model's native thread pool is torn down. This is the standard
# workaround for duplicate-OpenMP-runtime conflicts; must be set before torch/
# ctranslate2 are imported anywhere (they're imported lazily inside
# core.transcribe_file, well after this module loads).
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

from gui.app import main

if __name__ == "__main__":
    main()
