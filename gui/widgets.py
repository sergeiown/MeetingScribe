"""Small reusable widgets."""

import math
import time

import core

from PySide6.QtCore import Signal, Qt, QRectF, QPointF
from PySide6.QtGui import QColor, QPainter, QLinearGradient, QBrush, QPen, QPainterPath
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QProgressBar, QSizePolicy, QWidget

from .i18n import tr
from .style import current_colors

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
    """Vertical level meter on a dB scale: a smooth gradient-filled,
    rounded bar with a peak-hold line and a numeric dB readout - a custom
    paintEvent instead of a QProgressBar, whose chunked style and
    min/max/value semantics fight a clean look. Vertical + size-policy
    Expanding so it fills whatever height the panel around it has free,
    rather than sitting as a thin fixed-height strip."""

    _DECAY = 0.94  # per set_level() call - the meter is driven at ~25fps, so this reads as a fast but visible fall-off
    _MIN_DB = -48.0
    _SCALE_MARKS = (-40, -20, -12, -6, 0)  # -3 dropped: too close to 0 to fit both labels without overlap
    _SCALE_TEXT_WIDTH = 24
    _READOUT_HEIGHT = 14
    _CORNER_RADIUS = 5.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(40)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
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

    def _to_frac(self, level: float) -> float:
        """Linear amplitude (0..1) -> position (0..1) on the dB scale."""
        if level <= 0:
            db = self._MIN_DB
        else:
            db = max(self._MIN_DB, 20.0 * math.log10(level))
        return (db - self._MIN_DB) / (0.0 - self._MIN_DB)

    def paintEvent(self, event):
        colors = current_colors()
        accent = QColor(colors["accent"])
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect()
        bar_w = max(1, rect.width() - self._SCALE_TEXT_WIDTH)
        bar_h = max(1, rect.height() - self._READOUT_HEIGHT)
        label_h = self.fontMetrics().height()
        track_rect = QRectF(0.0, 0.0, bar_w, bar_h)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(colors["input_bg"]).darker(115))
        painter.drawRoundedRect(track_rect, self._CORNER_RADIUS, self._CORNER_RADIUS)

        level_frac = self._to_frac(self._level)
        fill_h = level_frac * bar_h
        if fill_h > 0.5:
            # Clip to the track's rounded shape so a short fill doesn't show
            # square corners poking out of the rounded track underneath it.
            painter.save()
            clip_path = QPainterPath()
            clip_path.addRoundedRect(track_rect, self._CORNER_RADIUS, self._CORNER_RADIUS)
            painter.setClipPath(clip_path)
            fill_rect = QRectF(0.0, bar_h - fill_h, bar_w, fill_h)
            # Theme accent color for the normal range; only the last stretch
            # right below 0 dB (clipping territory) turns into a warning
            # color, instead of the whole bar being green regardless of theme.
            gradient = QLinearGradient(0.0, bar_h, 0.0, 0.0)
            gradient.setColorAt(0.0, accent.darker(130))
            gradient.setColorAt(0.75, accent)
            gradient.setColorAt(0.92, QColor("#f1c40f"))
            gradient.setColorAt(1.0, QColor("#e74c3c"))
            painter.setBrush(QBrush(gradient))
            painter.drawRect(fill_rect)
            painter.restore()

        peak_frac = self._to_frac(self._peak_hold)
        if peak_frac > 0:
            peak_y = bar_h - peak_frac * bar_h
            painter.setPen(QPen(QColor(colors["text"]), 2))
            painter.drawLine(QPointF(2, peak_y), QPointF(bar_w - 2, peak_y))

        painter.setPen(QColor(colors["hint"]))
        font = painter.font()
        font.setPointSize(7)
        painter.setFont(font)
        # Processed top to bottom (highest dB first) so each label can be
        # pushed down below the previous one if the scale is too compressed
        # for both to fit at their exact dB position without overlapping -
        # on top of the edge-clamping below, which alone isn't enough when
        # two marks (like -3 and 0) land within a label-height of each other.
        prev_bottom = 0.0
        for mark_db in sorted(self._SCALE_MARKS, reverse=True):
            f = (mark_db - self._MIN_DB) / (0.0 - self._MIN_DB)
            y = bar_h - f * bar_h
            label_top = min(max(y - label_h / 2, prev_bottom), bar_h - label_h)
            prev_bottom = label_top + label_h
            painter.drawText(QRectF(bar_w + 4, label_top, self._SCALE_TEXT_WIDTH - 4, label_h),
                              Qt.AlignVCenter | Qt.AlignLeft, str(mark_db))

        # Below the bar, clearly separated from the "0" scale mark at the
        # top - level with it read as if it were (mis)labeling that mark.
        peak_db = self._MIN_DB if self._peak_hold <= 0 else max(self._MIN_DB, 20.0 * math.log10(self._peak_hold))
        painter.setPen(QColor(colors["text"]))
        painter.drawText(QRectF(0, bar_h + 2, rect.width(), self._READOUT_HEIGHT), Qt.AlignCenter,
                          tr("Peak: {db} dB", db=f"{peak_db:.0f}"))
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
