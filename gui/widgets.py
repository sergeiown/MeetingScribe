"""Small reusable widgets."""

import time

import core

from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QProgressBar, QSizePolicy, QWidget

from .i18n import tr

# Every button that can appear in this row-of-cards area, so they all end up
# the same width - Save token (in the token card) included, even though it
# lives outside ModelRowWidget itself (see settings_dialog.py).
ACTION_BUTTON_LABELS = ("Download", "Stop", "Delete", "Save token")


def fit_action_button_width(button: QPushButton) -> None:
    widths = []
    original = button.text()
    for text in ACTION_BUTTON_LABELS:
        button.setText(tr(text))
        widths.append(button.sizeHint().width())
    button.setText(original)
    button.setFixedWidth(max(widths))


class _ElidedLabel(QLabel):
    """A single-line label that elides its text to fit the current width
    instead of word-wrapping, so rows in a list (e.g. the Models tab) keep
    a uniform height regardless of description length or translation."""

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


class LevelMeterWidget(QWidget):
    """Horizontal peak-level bar with green/yellow/red zones and a briefly
    held peak tick. A custom paintEvent instead of a QProgressBar: a
    progress bar's chunked style and min/max/value semantics fight a clean
    peak-hold/color-zone look; a bare paintEvent is simpler and correct."""

    _DECAY = 0.94  # per set_level() call - the meter is driven at ~25fps, so this reads as a fast but visible fall-off

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(18)
        self._level = 0.0
        self._peak_hold = 0.0

    def set_level(self, level: float) -> None:
        self._level = max(0.0, min(1.0, level))
        self._peak_hold = max(self._peak_hold * self._DECAY, self._level)
        self.update()

    def reset(self) -> None:
        self._level = 0.0
        self._peak_hold = 0.0
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        rect = self.rect()
        painter.fillRect(rect, QColor("#1e1e1e"))

        width = rect.width()
        level_px = int(width * self._level)
        if level_px > 0:
            green_end = int(width * 0.7)
            yellow_end = int(width * 0.9)
            if level_px <= green_end:
                painter.fillRect(0, 0, level_px, rect.height(), QColor("#2ecc71"))
            elif level_px <= yellow_end:
                painter.fillRect(0, 0, green_end, rect.height(), QColor("#2ecc71"))
                painter.fillRect(green_end, 0, level_px - green_end, rect.height(), QColor("#f1c40f"))
            else:
                painter.fillRect(0, 0, green_end, rect.height(), QColor("#2ecc71"))
                painter.fillRect(green_end, 0, yellow_end - green_end, rect.height(), QColor("#f1c40f"))
                painter.fillRect(yellow_end, 0, level_px - yellow_end, rect.height(), QColor("#e74c3c"))

        peak_px = int(width * self._peak_hold)
        if peak_px > 1:
            painter.fillRect(peak_px - 2, 0, 2, rect.height(), QColor("#ffffff"))
        painter.end()


class ModelRowWidget(QFrame):
    """One row in the Models tab: name/size/status/action-button on top, a
    short description underneath. The single action button always shows
    whatever's relevant to the current state (Download/Stop/Delete), so a
    row is never left with dead reserved space where a hidden button would
    otherwise have been - the progress bar always reaches the same right
    edge the button itself sits at."""

    download_requested = Signal()
    cancel_requested = Signal()
    delete_requested = Signal()

    def __init__(self, label, size, description, installed, parent=None):
        super().__init__(parent)
        # Full card style (see style.py's QFrame#modelRow) keeps row extent
        # visually unambiguous even when neighboring rows differ in height.
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

        self._action_btn = QPushButton()
        self._action_btn.clicked.connect(self._on_action_clicked)
        fit_action_button_width(self._action_btn)
        top_row.addWidget(self._action_btn)
        outer.addLayout(top_row)

        if description:
            desc_label = _ElidedLabel(description)
            desc_label.setProperty("hint", True)
            outer.addWidget(desc_label)

        self._download_start_time = None
        self._mode = None  # "download" | "cancel" | "delete"
        self.set_installed(installed)

    def _on_action_clicked(self):
        if self._mode == "download":
            self.download_requested.emit()
        elif self._mode == "cancel":
            self.cancel_requested.emit()
        elif self._mode == "delete":
            self.delete_requested.emit()

    def set_installed(self, installed: bool):
        self._status_label.setText(tr("Installed") if installed else tr("Not installed"))
        self._status_label.setToolTip("")
        self._status_label.setVisible(True)
        self._progress.setVisible(False)
        self._mode = "delete" if installed else "download"
        self._action_btn.setText(tr("Delete") if installed else tr("Download"))
        self._action_btn.setEnabled(True)
        self._action_btn.setToolTip("")

    def set_needs_token(self, needs_token: bool):
        # Only meaningful while offering a fresh download - Delete/Stop never need a token.
        if self._mode == "download":
            self._action_btn.setEnabled(not needs_token)
            self._action_btn.setToolTip(tr("Needs HF_TOKEN - enter it above") if needs_token else "")

    def set_downloading(self, current: float, total: float, label: str = ""):
        if not self._progress.isVisible():
            self._download_start_time = time.time()
        # Hide the status text while the progress bar (which shows its own %/ETA) is up.
        self._status_label.setVisible(False)
        self._progress.setVisible(True)
        self._mode = "cancel"
        self._action_btn.setText(tr("Stop"))
        self._action_btn.setEnabled(True)
        self._action_btn.setToolTip("")
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
            self._mode = "download"
            self._action_btn.setText(tr("Download"))
            self._action_btn.setEnabled(True)
            self._status_label.setText(tr("Failed: {error}", error=error[:60]) if error else tr("Failed"))
            self._status_label.setToolTip(error)
            self._status_label.setVisible(True)
