"""QProgressBar whose indeterminate (busy) animation is a fully rounded pill
bouncing back and forth - Qt's own busy chunk wraps around the edge and gets
cut flat there."""

import math
import time

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QProgressBar

from .style import current_colors

_PERIOD_S = 1.6
_PILL_FRACTION = 0.3


class RoundedProgressBar(QProgressBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def _busy(self) -> bool:
        return self.minimum() == 0 and self.maximum() == 0

    def showEvent(self, event):
        super().showEvent(event)
        self._timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()

    def _tick(self):
        if self._busy():
            self.update()

    def paintEvent(self, event):
        if not self._busy():
            super().paintEvent(event)
            return
        c = current_colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        outer = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = outer.height() / 2
        p.setPen(QColor(c["border"]))
        p.setBrush(QColor(c["input_bg"]))
        p.drawRoundedRect(outer, radius, radius)

        inner = outer.adjusted(1.5, 1.5, -1.5, -1.5)
        pill_w = max(inner.height(), inner.width() * _PILL_FRACTION)
        phase = (time.monotonic() % _PERIOD_S) / _PERIOD_S
        eased = (1 - math.cos(2 * math.pi * phase)) / 2
        x = inner.left() + (inner.width() - pill_w) * eased
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(c["accent"]))
        p.drawRoundedRect(QRectF(x, inner.top(), pill_w, inner.height()), inner.height() / 2, inner.height() / 2)
        p.end()
