"""Detects the Windows system going to sleep (e.g. a laptop lid closing) so
an active recording can be stopped cleanly first, rather than however it
would otherwise end: the OS suspends the audio hardware along with
everything else, which surfaces here as an uncontrolled "device stopped
unexpectedly" mid-callback error instead of a normal, clean stop - and the
file's own elapsed-time bookkeeping would otherwise count the entire sleep
duration as if audio were still being captured, since Windows' monotonic
clock keeps advancing across a sleep. Same QAbstractNativeEventFilter
mechanism as gui/global_hotkey.py, just a different message."""

from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication

_WM_POWERBROADCAST = 0x0218
_PBT_APMSUSPEND = 0x0004


class PowerEventManager(QAbstractNativeEventFilter):
    def __init__(self, on_suspend):
        super().__init__()
        self._on_suspend = on_suspend
        QCoreApplication.instance().installNativeEventFilter(self)

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == _WM_POWERBROADCAST and msg.wParam == _PBT_APMSUSPEND:
                self._on_suspend()
        return False, 0
