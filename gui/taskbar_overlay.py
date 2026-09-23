"""Red-dot overlay badge on the app's Windows taskbar icon while recording
- the same mechanism apps like Teams/Discord use for their own notification
badges (Windows' ITaskbarList3::SetOverlayIcon), not a Qt-level feature:
Qt6 dropped the QtWinExtras module that used to wrap this, so it's called
directly via COM (confirmed by direct testing: SetOverlayIcon on a real
window's HWND, S_OK both to set and to clear). Windows itself decides
exactly where the badge sits on the icon (bottom-right in current Windows
versions) - that placement isn't something an app controls."""

import ctypes
from ctypes import wintypes

import comtypes
import comtypes.client
from comtypes import COMMETHOD, GUID, HRESULT, IUnknown
from PySide6.QtGui import QColor, QPainter, QPixmap

_CLSID_TaskbarList = GUID("{56FDF344-FD6D-11D0-958A-006097C9A090}")
_IID_ITaskbarList3 = GUID("{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF}")


class _ITaskbarList3(IUnknown):
    _iid_ = _IID_ITaskbarList3
    _methods_ = [
        COMMETHOD([], HRESULT, "HrInit"),
        COMMETHOD([], HRESULT, "AddTab", (['in'], wintypes.HWND, 'hwnd')),
        COMMETHOD([], HRESULT, "DeleteTab", (['in'], wintypes.HWND, 'hwnd')),
        COMMETHOD([], HRESULT, "ActivateTab", (['in'], wintypes.HWND, 'hwnd')),
        COMMETHOD([], HRESULT, "SetActiveAlt", (['in'], wintypes.HWND, 'hwnd')),
        COMMETHOD([], HRESULT, "MarkFullscreenWindow",
                  (['in'], wintypes.HWND, 'hwnd'), (['in'], wintypes.BOOL, 'fFullscreen')),
        COMMETHOD([], HRESULT, "SetProgressValue", (['in'], wintypes.HWND, 'hwnd'),
                  (['in'], ctypes.c_ulonglong, 'ullCompleted'), (['in'], ctypes.c_ulonglong, 'ullTotal')),
        COMMETHOD([], HRESULT, "SetProgressState",
                  (['in'], wintypes.HWND, 'hwnd'), (['in'], ctypes.c_int, 'tbpFlags')),
        COMMETHOD([], HRESULT, "RegisterTab",
                  (['in'], wintypes.HWND, 'hwndTab'), (['in'], wintypes.HWND, 'hwndMDI')),
        COMMETHOD([], HRESULT, "UnregisterTab", (['in'], wintypes.HWND, 'hwndTab')),
        COMMETHOD([], HRESULT, "SetTabOrder",
                  (['in'], wintypes.HWND, 'hwndTab'), (['in'], wintypes.HWND, 'hwndInsertBefore')),
        COMMETHOD([], HRESULT, "SetTabActive", (['in'], wintypes.HWND, 'hwndTab'),
                  (['in'], wintypes.HWND, 'hwndMDI'), (['in'], ctypes.c_uint, 'dwReserved')),
        COMMETHOD([], HRESULT, "ThumbBarAddButtons", (['in'], wintypes.HWND, 'hwnd'),
                  (['in'], ctypes.c_uint, 'cButtons'), (['in'], ctypes.c_void_p, 'pButton')),
        COMMETHOD([], HRESULT, "ThumbBarUpdateButtons", (['in'], wintypes.HWND, 'hwnd'),
                  (['in'], ctypes.c_uint, 'cButtons'), (['in'], ctypes.c_void_p, 'pButton')),
        COMMETHOD([], HRESULT, "ThumbBarSetImageList",
                  (['in'], wintypes.HWND, 'hwnd'), (['in'], ctypes.c_void_p, 'himl')),
        COMMETHOD([], HRESULT, "SetOverlayIcon", (['in'], wintypes.HWND, 'hwnd'),
                  (['in'], wintypes.HICON, 'hIcon'), (['in'], wintypes.LPCWSTR, 'pszDescription')),
        COMMETHOD([], HRESULT, "SetThumbnailTooltip",
                  (['in'], wintypes.HWND, 'hwnd'), (['in'], wintypes.LPCWSTR, 'pszTip')),
        COMMETHOD([], HRESULT, "SetThumbnailClip",
                  (['in'], wintypes.HWND, 'hwnd'), (['in'], ctypes.c_void_p, 'prcClip')),
    ]


class TaskbarOverlay:
    """One instance per top-level window. Best-effort throughout: a failure
    here (e.g. running under a Windows version/theme quirk that rejects the
    call) should never block or break recording itself."""

    def __init__(self, window):
        self._window = window
        self._hicon = None
        self._taskbar = None
        try:
            self._taskbar = comtypes.client.CreateObject(_CLSID_TaskbarList, interface=_ITaskbarList3)
            self._taskbar.HrInit()
        except Exception:
            self._taskbar = None

    def _dot_hicon(self):
        if self._hicon is not None:
            return self._hicon
        pix = QPixmap(32, 32)
        pix.fill(QColor(0, 0, 0, 0))
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor("#e74c3c"))
        painter.setPen(QColor("#ffffff"))
        painter.drawEllipse(4, 4, 24, 24)
        painter.end()
        # HICON needs a real .ico file underneath - QPixmap has no direct
        # to-HICON conversion, so round-trip through one (confirmed by
        # direct testing: LoadImageW loads it back correctly as an icon).
        import tempfile
        from pathlib import Path
        tmp_path = Path(tempfile.gettempdir()) / "meetingscribe_rec_dot.ico"
        pix.save(str(tmp_path), "ICO")
        self._hicon = ctypes.windll.user32.LoadImageW(None, str(tmp_path), 1, 32, 32, 0x10)
        return self._hicon

    def set_recording(self, active: bool) -> None:
        if self._taskbar is None:
            return
        try:
            hwnd = int(self._window.winId())
            if active:
                self._taskbar.SetOverlayIcon(hwnd, self._dot_hicon(), "Recording")
            else:
                self._taskbar.SetOverlayIcon(hwnd, None, "")
        except Exception:
            pass
