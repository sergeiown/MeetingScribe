"""Recording preferences (which sources are active, device choice, exclusive
mode default), stored like device_prefs.py. Device *name* is persisted, not
a numeric index - indices aren't stable across reboots or when USB audio
devices are plugged in a different order. A device name of None/"" means
"system default", resolved fresh at recording time rather than pinned."""

from PySide6.QtCore import QSettings

_USE_MIC_KEY = "recording_use_mic"
_USE_SYSTEM_KEY = "recording_use_system"
_MIC_DEVICE_KEY = "recording_mic_device"
_LOOPBACK_DEVICE_KEY = "recording_loopback_device"
_EXCLUSIVE_KEY = "recording_exclusive"


def get_use_microphone() -> bool:
    return QSettings("MeetingScribe", "MeetingScribe").value(_USE_MIC_KEY, True, type=bool)


def set_use_microphone(value: bool) -> None:
    QSettings("MeetingScribe", "MeetingScribe").setValue(_USE_MIC_KEY, value)


def get_use_system_audio() -> bool:
    return QSettings("MeetingScribe", "MeetingScribe").value(_USE_SYSTEM_KEY, False, type=bool)


def set_use_system_audio(value: bool) -> None:
    QSettings("MeetingScribe", "MeetingScribe").setValue(_USE_SYSTEM_KEY, value)


def get_mic_device_name():
    """None means "system default"."""
    return QSettings("MeetingScribe", "MeetingScribe").value(_MIC_DEVICE_KEY, None) or None


def set_mic_device_name(name) -> None:
    QSettings("MeetingScribe", "MeetingScribe").setValue(_MIC_DEVICE_KEY, name or "")


def get_loopback_device_name():
    """None means "system default"."""
    return QSettings("MeetingScribe", "MeetingScribe").value(_LOOPBACK_DEVICE_KEY, None) or None


def set_loopback_device_name(name) -> None:
    QSettings("MeetingScribe", "MeetingScribe").setValue(_LOOPBACK_DEVICE_KEY, name or "")


def get_exclusive_default() -> bool:
    return QSettings("MeetingScribe", "MeetingScribe").value(_EXCLUSIVE_KEY, True, type=bool)


def set_exclusive_default(value: bool) -> None:
    QSettings("MeetingScribe", "MeetingScribe").setValue(_EXCLUSIVE_KEY, value)
