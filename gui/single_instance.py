"""Single-instance enforcement, plus telling an already-running instance to
restore its window when a second launch is attempted - e.g. double-clicking
the shortcut again while the first instance sits minimized to the tray (now
the default). Without this, the second launch just showed an unhelpful
"already running" dialog and left the first instance's window untouched -
confirmed live as a real, confusing dead end once minimize-to-tray became
on-by-default instead of opt-in.

Uses a Win32 named mutex for the lock itself (unchanged from before) and a
registered window message, found and posted directly to the first
instance's window by title via FindWindow, to ask it to restore - the same
QAbstractNativeEventFilter mechanism already used elsewhere in gui/ for
global-hotkey and power-suspend notifications, just a different message."""

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication

_MUTEX_NAME = "Global\\MeetingScribe-SingleInstance"
_WINDOW_TITLE = "MeetingScribe"
_SHOW_MESSAGE_NAME = "MeetingScribe-ShowMainWindow"

_mutex_handle = None


def acquire_lock() -> bool:
    """True if this process got the lock (no other instance already running)."""
    global _mutex_handle
    ERROR_ALREADY_EXISTS = 183
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        ctypes.windll.kernel32.CloseHandle(handle)
        return False
    _mutex_handle = handle
    return True


def notify_running_instance() -> bool:
    """Called once acquire_lock() fails - finds the first instance's
    (possibly hidden, minimized-to-tray) window by title and asks it to
    restore itself. Returns True if a window was found and notified, so the
    caller knows whether it's safe to exit quietly or should fall back to
    telling the user directly."""
    hwnd = ctypes.windll.user32.FindWindowW(None, _WINDOW_TITLE)
    if not hwnd:
        return False
    message_id = ctypes.windll.user32.RegisterWindowMessageW(_SHOW_MESSAGE_NAME)
    ctypes.windll.user32.PostMessageW(hwnd, message_id, 0, 0)
    return True


class ShowWindowListener(QAbstractNativeEventFilter):
    """Installed by the real running instance - restores the window when
    notify_running_instance() (from a second launch) pings it."""

    def __init__(self, on_show_requested):
        super().__init__()
        self._on_show_requested = on_show_requested
        self._message_id = ctypes.windll.user32.RegisterWindowMessageW(_SHOW_MESSAGE_NAME)
        QCoreApplication.instance().installNativeEventFilter(self)

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == self._message_id:
                self._on_show_requested()
        return False, 0
