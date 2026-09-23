"""System-wide (not just app-focused) keyboard shortcut for starting and
stopping a recording. A plain Qt QShortcut only fires while this app's own
window has focus, which defeats the point of a quick recording toggle -
the whole use case is starting/stopping a meeting recording without
switching away from whatever call is actually in focus (Teams, etc.). Win32's
RegisterHotKey delivers a WM_HOTKEY message to the calling thread's queue
regardless of which window is focused; QAbstractNativeEventFilter is Qt's
hook to see that message before Qt's own dispatch discards it."""

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, Qt
from PySide6.QtGui import QKeySequence

_WM_HOTKEY = 0x0312
_HOTKEY_ID = 0xB00C  # arbitrary; this app only ever registers one hotkey

_MOD_ALT = 0x0001
_MOD_CONTROL = 0x0002
_MOD_SHIFT = 0x0004
_MOD_WIN = 0x0008
_MOD_NOREPEAT = 0x4000

# Qt::Key -> Windows virtual-key code, for the keys where they differ from
# plain ASCII (letters A-Z and digits 0-9 already match VK codes numerically).
_SPECIAL_VK = {
    Qt.Key_Backspace: 0x08, Qt.Key_Tab: 0x09, Qt.Key_Clear: 0x0C,
    Qt.Key_Return: 0x0D, Qt.Key_Enter: 0x0D, Qt.Key_Escape: 0x1B,
    Qt.Key_Space: 0x20, Qt.Key_PageUp: 0x21, Qt.Key_PageDown: 0x22,
    Qt.Key_End: 0x23, Qt.Key_Home: 0x24,
    Qt.Key_Left: 0x25, Qt.Key_Up: 0x26, Qt.Key_Right: 0x27, Qt.Key_Down: 0x28,
    Qt.Key_Insert: 0x2D, Qt.Key_Delete: 0x2E,
    Qt.Key_F1: 0x70, Qt.Key_F2: 0x71, Qt.Key_F3: 0x72, Qt.Key_F4: 0x73,
    Qt.Key_F5: 0x74, Qt.Key_F6: 0x75, Qt.Key_F7: 0x76, Qt.Key_F8: 0x77,
    Qt.Key_F9: 0x78, Qt.Key_F10: 0x79, Qt.Key_F11: 0x7A, Qt.Key_F12: 0x7B,
}


def _key_sequence_to_win32(sequence: str):
    """Parses a Qt key-sequence string (e.g. "Alt+Backspace") into
    (modifiers, vk_code) for RegisterHotKey, or None if it's empty or uses
    a key with no known VK mapping above."""
    seq = QKeySequence(sequence)
    if seq.isEmpty():
        return None
    combo = seq[0]
    key = combo.key()
    qt_mods = combo.keyboardModifiers()

    mods = _MOD_NOREPEAT
    if qt_mods & Qt.AltModifier:
        mods |= _MOD_ALT
    if qt_mods & Qt.ControlModifier:
        mods |= _MOD_CONTROL
    if qt_mods & Qt.ShiftModifier:
        mods |= _MOD_SHIFT
    if qt_mods & Qt.MetaModifier:
        mods |= _MOD_WIN

    if key in _SPECIAL_VK:
        vk = _SPECIAL_VK[key]
    elif Qt.Key_A <= key <= Qt.Key_Z or Qt.Key_0 <= key <= Qt.Key_9:
        vk = key
    else:
        return None
    return mods, vk


class GlobalHotkeyManager(QAbstractNativeEventFilter):
    """Registers exactly one system-wide hotkey - calling set_hotkey again
    replaces it. hwnd=None in RegisterHotKey ties the hotkey to this
    thread's message queue rather than a specific window, which is
    exactly what a native event filter installed on QCoreApplication
    (also thread-wide) is positioned to intercept."""

    def __init__(self):
        super().__init__()
        self._registered = False
        self._callback = None
        QCoreApplication.instance().installNativeEventFilter(self)

    def set_hotkey(self, sequence: str, callback) -> bool:
        """Returns False if the sequence couldn't be parsed or the OS
        refused it (e.g. already claimed by another app) - the caller
        decides how to surface that."""
        self.unregister()
        self._callback = callback
        parsed = _key_sequence_to_win32(sequence)
        if parsed is None:
            return False
        mods, vk = parsed
        self._registered = bool(ctypes.windll.user32.RegisterHotKey(None, _HOTKEY_ID, mods, vk))
        return self._registered

    def unregister(self) -> None:
        if self._registered:
            ctypes.windll.user32.UnregisterHotKey(None, _HOTKEY_ID)
            self._registered = False

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == _WM_HOTKEY and msg.wParam == _HOTKEY_ID:
                if self._callback:
                    self._callback()
                return True, 0
        return False, 0
