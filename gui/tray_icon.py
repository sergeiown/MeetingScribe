"""System tray icon - lets the window be minimized/closed to the tray
instead of the taskbar (see MainWindow's changeEvent/closeEvent). Same icon
and recording-in-progress red dot as the taskbar badge (taskbar_overlay.py),
just composed into one plain pixmap up front: unlike ITaskbarList3's
SetOverlayIcon, QSystemTrayIcon has no OS-level API of its own to draw an
overlay on top of whatever icon is already showing."""

from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from .i18n import tr

_ICON_SIZE = 32


def _load_base_pixmap(icon_path: Path) -> QPixmap:
    pix = QIcon(str(icon_path)).pixmap(_ICON_SIZE, _ICON_SIZE)
    if pix.isNull():
        pix = QPixmap(_ICON_SIZE, _ICON_SIZE)
        pix.fill(QColor(0, 0, 0, 0))
    return pix


def _with_recording_dot(pixmap: QPixmap) -> QPixmap:
    result = QPixmap(pixmap)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor("#e74c3c"))
    painter.setPen(QColor("#ffffff"))
    d = _ICON_SIZE * 0.42
    painter.drawEllipse(_ICON_SIZE - d - 1, _ICON_SIZE - d - 1, d, d)
    painter.end()
    return result


class TrayIconManager(QObject):
    """Best-effort throughout, same philosophy as TaskbarOverlay: a failure
    here should never block or break the app - just leave the tray icon
    absent/unrecorded."""

    restore_requested = Signal()
    quit_requested = Signal()

    def __init__(self, icon_path: Path, parent=None):
        super().__init__(parent)
        self._base_pixmap = _load_base_pixmap(icon_path)
        self._dot_pixmap = _with_recording_dot(self._base_pixmap)
        self._tray = QSystemTrayIcon(QIcon(self._base_pixmap), parent)
        self._tray.setToolTip("MeetingScribe")

        menu = QMenu()
        self._show_action = menu.addAction(tr("Show"))
        self._show_action.triggered.connect(self.restore_requested.emit)
        menu.addSeparator()
        self._exit_action = menu.addAction(tr("Exit"))
        self._exit_action.triggered.connect(self.quit_requested.emit)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_activated)

    @staticmethod
    def is_available() -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def _on_activated(self, reason):
        # Trigger = a single left click on Windows; DoubleClick is included
        # too since that's the more familiar restore gesture for a lot of
        # other tray apps - both should do the same thing here.
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.restore_requested.emit()

    def show(self) -> None:
        self._tray.show()

    def hide(self) -> None:
        self._tray.hide()

    def is_visible(self) -> bool:
        return self._tray.isVisible()

    def set_recording(self, active: bool) -> None:
        self._tray.setIcon(QIcon(self._dot_pixmap if active else self._base_pixmap))

    def retranslate_ui(self) -> None:
        self._show_action.setText(tr("Show"))
        self._exit_action.setText(tr("Exit"))
