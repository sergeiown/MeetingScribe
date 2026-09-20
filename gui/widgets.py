"""Small reusable widgets."""

import time

import core

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QProgressBar

from .i18n import tr


class _ElidedLabel(QLabel):
    """A single-line label that elides its text to fit the current width,
    instead of QLabel's own word-wrap. A fixed one-line height means every
    row in a list built from these (e.g. the Models tab) stays exactly the
    same height regardless of description length, dialog width, or
    translation - word-wrap would let a longer line grow some rows taller
    than their neighbors."""

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self._full_text = text
        self.setFixedHeight(self.fontMetrics().height())
        self._update_elided()

    def _update_elided(self):
        elided = self.fontMetrics().elidedText(self._full_text, Qt.ElideRight, self.width())
        super().setText(elided)
        self.setToolTip(self._full_text if elided != self._full_text else "")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_elided()


class ModelRowWidget(QFrame):
    """One row in the Models tab: name/size/status/Download on top, a short
    description underneath. The Download button turns into a progress bar
    while active."""

    download_requested = Signal()

    def __init__(self, label, size, description, installed, parent=None):
        super().__init__(parent)
        # A full card (background + border, see style.py's QFrame#modelRow
        # rule), not just a bottom line - a line alone still let two rows of
        # different height (one with a Download button, taller, next to a
        # plain "Installed" row) look inconsistently spaced, since what
        # actually varies is the row's own height, not the gap after it. A
        # boxed card makes each model's extent unambiguous regardless.
        self.setObjectName("modelRow")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(4)

        top_row = QHBoxLayout()
        top_row.addWidget(QLabel(label), stretch=1)

        size_label = QLabel(size)
        size_label.setProperty("hint", True)
        top_row.addWidget(size_label)

        self._status_label = QLabel()
        top_row.addWidget(self._status_label)

        self._progress = QProgressBar()
        self._progress.setMinimumWidth(220)
        self._progress.setVisible(False)
        top_row.addWidget(self._progress)

        self._download_btn = QPushButton(tr("Download"))
        self._download_btn.clicked.connect(self.download_requested.emit)
        top_row.addWidget(self._download_btn)
        outer.addLayout(top_row)

        if description:
            desc_label = _ElidedLabel(description)
            desc_label.setProperty("hint", True)
            outer.addWidget(desc_label)

        self._download_start_time = None
        self.set_installed(installed)

    def set_installed(self, installed: bool):
        self._status_label.setText(tr("Installed") if installed else tr("Not installed"))
        self._status_label.setVisible(True)
        self._download_btn.setVisible(not installed)
        self._progress.setVisible(False)

    def set_needs_token(self, needs_token: bool):
        self._download_btn.setEnabled(not needs_token)
        self._download_btn.setToolTip(
            tr("Needs HF_TOKEN - enter it above") if needs_token else "")

    def set_downloading(self, current: float, total: float, label: str = ""):
        if not self._progress.isVisible():
            self._download_start_time = time.time()
        self._download_btn.setVisible(False)
        # Hides the static "Not installed" text while the bar (which shows
        # its own %/ETA text) is up - both together were competing for the
        # same cramped space and clipping the ETA off the edge of the card.
        self._status_label.setVisible(False)
        self._progress.setVisible(True)
        if total > 0:
            pct = min(current / total, 1.0)
            self._progress.setRange(0, 100)
            self._progress.setValue(int(pct * 100))
            eta_str = ""
            if pct > 0.02 and self._download_start_time is not None:
                elapsed = time.time() - self._download_start_time
                eta_sec = elapsed / pct * (1.0 - pct)
                eta_str = f"  ·  {tr('ETA {time}', time=core.format_time(eta_sec))}"
            self._progress.setFormat(f"%p%{eta_str}")
        else:
            self._progress.setRange(0, 0)  # indeterminate
            self._progress.setFormat("")

    def set_done(self, ok: bool, error: str = ""):
        self._progress.setVisible(False)
        self._download_start_time = None
        if ok:
            self.set_installed(True)
        else:
            self._download_btn.setVisible(True)
            self._status_label.setText(tr("Failed: {error}", error=error[:60]) if error else tr("Failed"))
            self._status_label.setVisible(True)
