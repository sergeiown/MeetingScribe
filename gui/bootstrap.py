"""First-launch dependency bootstrap.

The installer (see packaging/meetingscribe.iss) ships only source files plus
a minimal PySide6 install - it does not bundle torch/pyannote.audio/
faster-whisper (multiple GB). Those are installed here on first launch, with
a GUI progress dialog instead of a batch console window.
"""

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPlainTextEdit, QProgressBar, QMessageBox

import core

from .i18n import tr

_REQUIRED_MODULES = ("torch", "faster_whisper", "pyannote.audio")
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Matches pip's "Downloading ...whl (12.6 MB)" or "Using cached ...whl
# (12.6 MB)" lines. Excludes the smaller ".whl.metadata (6.6 kB)" pre-fetch
# line pip also prints (that one ends in ".metadata", not ".whl").
_DOWNLOAD_SIZE_RE = re.compile(
    r"(?:Downloading|Using cached)\s+\S+\.whl\s+\(([\d.]+)\s*([KMGT]?B)\)", re.IGNORECASE)
_UNIT_BYTES = {"B": 1, "KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4}


def _parse_size_to_bytes(value: str, unit: str) -> int:
    return int(float(value) * _UNIT_BYTES.get(unit.upper(), 1))


def _head_content_length(url: str) -> int:
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=8) as resp:
            return int(resp.headers.get("Content-Length", 0))
    except Exception:
        return 0


def _estimate_download_bytes(pip_install_args: list):
    """Computes the exact total download bytes without downloading anything,
    via `pip install --dry-run --report` (resolves the real package set and
    each wheel's URL) then a HEAD request per URL. Takes roughly 10-15s.
    Returns None on any failure; the caller falls back to an indeterminate
    spinner."""
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            report_path = Path(tmp_dir) / "report.json"
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--dry-run", "--ignore-installed",
                 "--report", str(report_path)] + pip_install_args,
                capture_output=True, text=True, creationflags=_CREATE_NO_WINDOW, timeout=60,
            )
            if result.returncode != 0 or not report_path.exists():
                return None
            data = json.loads(report_path.read_text(encoding="utf-8"))
            urls = [
                item["download_info"]["url"]
                for item in data.get("install", [])
                if "download_info" in item and "url" in item["download_info"]
            ]
        if not urls:
            return None
        with ThreadPoolExecutor(max_workers=16) as pool:
            sizes = list(pool.map(_head_content_length, urls))
        total = sum(sizes)
        return total or None
    except Exception:
        return None


def dependencies_installed() -> bool:
    """Cheap presence check (no actual import), since this runs on every
    launch, not just the first."""
    return all(importlib.util.find_spec(mod) is not None for mod in _REQUIRED_MODULES)


def _nvidia_gpu_present() -> bool:
    """Same detection setup.bat uses; checked before torch exists, so it
    can't rely on torch.cuda.is_available()."""
    if shutil.which("nvidia-smi"):
        return True
    for p in (
        r"C:\Windows\System32\nvidia-smi.exe",
        r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
    ):
        if Path(p).exists():
            return True
    return False


class _InstallWorker(QObject):
    line = Signal(str)
    progress_fraction = Signal(float)  # 0.0-1.0; only emitted once a real total is known
    finished = Signal(bool, str, str)  # ok, error, model_error

    def __init__(self, requirements_path: Path, install_deps: bool,
                 model_spec=None, hf_token: str = ""):
        super().__init__()
        self._requirements_path = requirements_path
        self._install_deps = install_deps
        self._model_spec = model_spec
        self._hf_token = hf_token
        self._total_bytes = None
        self._bytes_done = 0
        self._pending_bytes = 0

    def _flush_pending(self):
        """Marks the last noted download's size as finished - either the
        next "Downloading ..." line just started, or pip's process exited."""
        if self._pending_bytes:
            self._bytes_done += self._pending_bytes
            self._pending_bytes = 0
            if self._total_bytes:
                self.progress_fraction.emit(min(self._bytes_done / self._total_bytes, 1.0))

    def _run_pip(self, args):
        # No -q: quiet mode would suppress pip's "Collecting.../Downloading..."
        # lines, leaving the dialog looking frozen during large downloads.
        # --progress-bar on: piped stdout never renders a live percentage,
        # only a single completed-download summary line per file - hence the
        # bytes-done/total tracking below, which turns those discrete
        # completions into an overall percentage.
        proc = subprocess.Popen(
            [sys.executable, "-m", "pip", "install", "--progress-bar", "on"] + args,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            bufsize=1, creationflags=_CREATE_NO_WINDOW,
        )
        for raw_line in proc.stdout:
            line = raw_line.rstrip()
            m = _DOWNLOAD_SIZE_RE.search(line)
            if m:
                self._flush_pending()  # the previous pending download must be done by now
                self._pending_bytes = _parse_size_to_bytes(m.group(1), m.group(2))
            self.line.emit(line)
        self._flush_pending()  # whatever was still pending when pip exited is done too
        return proc.wait()

    def run(self):
        try:
            base_args = ["-r", str(self._requirements_path)]
            use_cuda = self._install_deps and _nvidia_gpu_present()
            cuda_args = [
                "torch", "torchaudio", "--upgrade",
                "--index-url", "https://download.pytorch.org/whl/cu126",
            ]

            # One combined total up front so the whole job is a single
            # continuous 0-100% arc in one window, never a components window
            # followed by a separate model-download window.
            self.line.emit("Estimating total download size...")
            if self._install_deps:
                base_total = _estimate_download_bytes(base_args)
                cuda_total = _estimate_download_bytes(cuda_args) if use_cuda else 0
                known = base_total is not None and cuda_total is not None
            else:
                base_total = cuda_total = 0
                known = True
            model_total = 0
            if self._model_spec is not None:
                model_total = core.whisper_model_bytes(self._model_spec, self._hf_token) or 0
            self._total_bytes = (base_total + cuda_total + model_total) if known else None
            self._bytes_done = 0

            if self._install_deps:
                code = self._run_pip(base_args)
                if code != 0:
                    self.finished.emit(False, f"pip exited with code {code}", "")
                    return
                if use_cuda:
                    self.line.emit("NVIDIA GPU detected - installing CUDA build of torch...")
                    code = self._run_pip(cuda_args)
                    if code != 0:
                        # Not fatal: base (CPU) torch is already installed and usable.
                        self.line.emit(f"CUDA torch install failed (code {code}) - continuing on CPU.")

            model_error = self._download_model() if self._model_spec is not None else ""
            self.finished.emit(True, "", model_error)
        except Exception as e:
            self.finished.emit(False, str(e), "")

    def _download_model(self) -> str:
        spec = self._model_spec
        self.line.emit(f"Downloading {spec.label} model ({spec.size})...")
        bytes_before_model = self._bytes_done

        def on_progress(current, total, _label):
            if self._total_bytes:
                overall = bytes_before_model + current
                self.progress_fraction.emit(min(overall / self._total_bytes, 1.0))

        try:
            core.download_whisper_model(spec.key, spec.hf_repo, self._hf_token, on_progress=on_progress)
            return ""
        except Exception as e:
            return str(e)


class BootstrapDialog(QDialog):
    """Modal progress dialog shown once while pip installs the heavy
    dependencies, with a small terminal-style scrollback of recent output."""

    _LOG_VISIBLE_LINES = 5

    def __init__(self, requirements_path: Path, install_deps: bool = True,
                 model_spec=None, hf_token: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("MeetingScribe - Setting up"))
        # This is typically the very first window shown (parent=None, before
        # MainWindow exists) - explicit rather than relying on the
        # QApplication-wide default to propagate in time for the taskbar.
        icon_path = core.SCRIPT_DIR / "img" / "icon.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.setModal(True)
        self.setMinimumWidth(480)
        # No Cancel button - these components are mandatory, the app can't
        # run without them. The title bar's X is still there by default
        # though, and closing it while _worker's QThread is still running
        # hard-crashes the process the same way skipping quit()+wait() does
        # elsewhere (see _on_finished) - closeEvent() below blocks that.
        self.ok = False
        self.error = ""
        self.model_error = ""
        self._start_time = time.time()
        self._last_pip_line = ""
        self._recent_lines = []  # rolling history, real pip lines only

        layout = QVBoxLayout(self)
        if install_deps:
            explanation = tr(
                "MeetingScribe needs about 2-3 GB of speech-recognition "
                "components it doesn't bundle by default. This is a one-time "
                "download - how long it takes depends mostly on your internet "
                "speed, often several minutes. You won't need to do this again. "
                "The line below shows the exact package and size currently "
                "downloading.")
        else:
            explanation = tr(
                "Downloading the {model} speech-recognition model - a "
                "one-time download. The line below shows progress.",
                model=model_spec.label if model_spec else "")
        label = QLabel(explanation)
        label.setWordWrap(True)
        layout.addWidget(label)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # indeterminate
        layout.addWidget(self._progress)

        # Fixed-height, elided (never wrapped) log strip so a long line (a
        # URL, a path) can't grow the dialog or need a scrollbar.
        self._log_view = QPlainTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setFont(QFont("Consolas", 9))
        self._log_view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self._log_view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._log_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._log_view.setFocusPolicy(Qt.NoFocus)
        self._log_view.setStyleSheet(
            "background-color: #1e1e1e; color: #6fdc8c; "
            "padding: 4px 8px; border-radius: 4px; border: 1px solid #3a3a3a;"
        )
        # Fixed generous per-line height instead of measuring via
        # document().size(), which undershoots before layout and clips text.
        metrics = self._log_view.fontMetrics()
        self._log_view.setFixedHeight(metrics.lineSpacing() * self._LOG_VISIBLE_LINES + 40)
        layout.addWidget(self._log_view)

        self._thread = QThread(self)
        self._worker = _InstallWorker(requirements_path, install_deps, model_spec, hf_token)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.line.connect(self._on_line)
        self._worker.progress_fraction.connect(self._on_progress_fraction)
        self._worker.finished.connect(self._on_finished)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)

        # pip can go quiet for a stretch mid-download - this ticks every
        # second regardless, so the dialog never *looks* frozen.
        self._heartbeat_timer = QTimer(self)
        self._heartbeat_timer.timeout.connect(self._update_heartbeat)
        self._heartbeat_timer.start(1000)
        self._update_heartbeat()

    def start(self):
        self._thread.start()

    def closeEvent(self, event):
        # Ignoring the close while the worker is still on its QThread avoids
        # the same crash quit()+wait() guards against in _on_finished - this
        # is reached instead when the user clicks the title bar's X, which
        # accept()/reject() are never wired to.
        if self._thread.isRunning():
            event.ignore()
        else:
            super().closeEvent(event)

    def _on_line(self, line):
        line = line.strip()
        if line:
            self._last_pip_line = line
            self._recent_lines.append(line)
            if len(self._recent_lines) > 200:
                self._recent_lines = self._recent_lines[-200:]
            self._update_heartbeat()

    def _on_progress_fraction(self, fraction):
        self._progress.setRange(0, 100)
        self._progress.setValue(int(fraction * 100))

    def _update_heartbeat(self):
        elapsed = int(time.time() - self._start_time)
        lines = list(self._recent_lines[-self._LOG_VISIBLE_LINES:]) or [tr("Starting...")]
        # Only the last (current/live) line ticks with elapsed time - the
        # ones above it are finished history and don't need to move.
        lines[-1] = f"{lines[-1]}  ·  {tr('{time} elapsed', time=core.format_time(elapsed))}"
        # Elide each line (never wrap) so a long one - a URL, a path - can
        # never spill past the strip or grow the dialog.
        available = max(self._log_view.viewport().width() - 8, 200)
        metrics = self._log_view.fontMetrics()
        elided = [metrics.elidedText(l, Qt.ElideRight, available) for l in lines]
        self._log_view.setPlainText("\n".join(elided))

    def _on_finished(self, ok, error, model_error):
        self._heartbeat_timer.stop()
        self.ok = ok
        self.error = error
        self.model_error = model_error
        # This slot runs the instant `finished` fires, before the
        # underlying thread has fully unwound. thread.quit() only requests
        # the event loop to stop; if accept() destroyed this dialog (and its
        # QThread) first, Qt hard-aborts the process with "QThread:
        # Destroyed while thread is still running". quit()+wait() must
        # happen before accept(). wait() blocks only briefly here since the
        # worker's Python code has already returned.
        self._thread.quit()
        self._thread.wait()
        self.accept()


def ensure_dependencies(requirements_path: Path, model_spec=None, hf_token: str = "", parent=None):
    """(ok, model_error). ok is True once the heavy ML dependencies are
    installed and usable. model_error is a non-fatal message if model_spec
    was given and its download failed (empty otherwise). Shows one combined
    dialog covering whichever of (pip deps, model_spec) is actually needed."""
    install_deps = not dependencies_installed()
    if not install_deps and model_spec is None:
        return True, ""
    dlg = BootstrapDialog(requirements_path, install_deps, model_spec, hf_token, parent)
    dlg.start()
    dlg.exec()
    if not dlg.ok:
        QMessageBox.critical(
            parent, tr("Setup failed"),
            tr("Could not install required components:\n\n{error}\n\n"
               "Try restarting the app, or install manually:\npip install -r \"{path}\"",
               error=dlg.error, path=str(requirements_path)))
        return False, ""
    return True, dlg.model_error
