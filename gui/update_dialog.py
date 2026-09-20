"""Download progress dialog for the in-app updater. Modeled directly on
bootstrap.py's BootstrapDialog - same QThread + worker wiring, same
quit()+wait()-before-close() ordering (see _on_finished below for why that
ordering specifically is not optional)."""

from pathlib import Path

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QProgressBar, QDialogButtonBox

import core

from .i18n import tr
from .workers import ModelDownloadWorker


def _format_size(n: float) -> str:
    return f"{n / 1_000_000:.1f} MB"


class UpdateDownloadDialog(QDialog):
    """Downloads one installer exe with a progress bar and a Cancel button.
    self.installer_path is set to the downloaded file on success; stays
    None on cancel or failure (self.error holds the message for failures -
    a plain user cancel leaves both empty, since ModelDownloadWorker
    reports a cooperative Cancelled the same way as success, via its
    `finished` signal - the only way to tell them apart is whether the
    destination file actually exists afterward)."""

    def __init__(self, url: str, dest_path: Path, total_size: int, version: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Downloading update"))
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
        # Same ordering as BootstrapDialog._on_finished, and for the exact
        # same reason: this slot runs the instant the worker's run() emits
        # `finished`, a moment before the underlying OS thread has actually
        # unwound. Without quit()+wait() here, this dialog (and the QThread
        # parented to it) could be garbage-collected while that thread is
        # still technically alive once accept()/reject() closes it - Qt
        # then hard-aborts the whole process ("QThread: Destroyed while
        # thread is still running"), a real crash reproduced and fixed
        # today in the first-run bootstrap dialog this class is modeled on.
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
