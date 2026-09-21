"""Download progress dialog for the in-app updater. Modeled on
bootstrap.py's BootstrapDialog: same QThread + worker wiring and
quit()+wait()-before-close() ordering (see _on_finished)."""

from pathlib import Path

from PySide6.QtCore import QThread
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QProgressBar, QDialogButtonBox

import core

from .i18n import tr
from .workers import ModelDownloadWorker


def _format_size(n: float) -> str:
    return f"{n / 1_000_000:.1f} MB"


class UpdateDownloadDialog(QDialog):
    """Downloads one installer exe with a progress bar and a Cancel button.
    self.installer_path is set to the downloaded file on success and stays
    None on cancel or failure (self.error holds the message for failures).
    Cancel and success both fire the `finished` signal, so they're told
    apart only by whether the destination file exists afterward."""

    def __init__(self, url: str, dest_path: Path, total_size: int, version: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Downloading update"))
        icon_path = core.SCRIPT_DIR / "img" / "icon.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.setModal(True)
        self.setMinimumWidth(420)
        self.installer_path = None
        self.error = ""
        self._dest_path = dest_path

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("Downloading MeetingScribe {version}...", version=version)))
        self._progress = QProgressBar()
        self._progress.setRange(0, 100 if total_size else 0)
        layout.addWidget(self._progress)
        self._status = QLabel("")
        self._status.setProperty("hint", True)
        layout.addWidget(self._status)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.rejected.connect(self._on_cancel_clicked)
        layout.addWidget(buttons)

        def download_fn(cancel_token, on_progress, url=url, dest=dest_path):
            core.download_installer(url, dest, cancel_token, on_progress)

        self._thread = QThread(self)
        self._worker = ModelDownloadWorker(download_fn)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)

    def start(self):
        self._thread.start()

    def _on_cancel_clicked(self):
        self._worker.cancel()
        self._status.setText(tr("Cancelling..."))

    def _on_progress(self, current, total, _label):
        if total:
            self._progress.setRange(0, 100)
            self._progress.setValue(int(current / total * 100))
            self._status.setText(f"{_format_size(current)} / {_format_size(total)}")
        else:
            self._status.setText(_format_size(current))

    def _on_finished(self):
        # thread.quit()+wait() must run before accept()/reject() - skipping this
        # can hard-crash the process ("QThread: Destroyed while thread is still
        # running") since the OS thread may not have unwound yet when this slot runs.
        self._thread.quit()
        self._thread.wait()
        if self._dest_path.exists():
            self.installer_path = self._dest_path
            self.accept()
        else:
            self.reject()  # cancelled before any bytes landed at dest_path

    def _on_failed(self, error):
        self.error = error
        self._thread.quit()
        self._thread.wait()
        self.reject()
