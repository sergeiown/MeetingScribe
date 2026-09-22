"""Recording-panel preferences (source, last-used device name, exclusive-mode
default), stored like device_prefs.py. Device *name* is persisted, not a
numeric index - indices aren't stable across reboots or when USB audio
devices are plugged in a different order."""

from PySide6.QtCore import QSettings

_SOURCE_KEY = "recording_source"
_MIC_DEVICE_KEY = "recording_mic_device"
_LOOPBACK_DEVICE_KEY = "recording_loopback_device"
_EXCLUSIVE_KEY = "recording_exclusive"

DEFAULT_SOURCE = "microphone"  # "microphone" | "system"


def get_recording_source() -> str:
    return QSettings("MeetingScribe", "MeetingScribe").value(_SOURCE_KEY, DEFAULT_SOURCE)


def set_recording_source(value: str) -> None:
    QSettings("MeetingScribe", "MeetingScribe").setValue(_SOURCE_KEY, value)


def get_last_mic_device_name():
    return QSettings("MeetingScribe", "MeetingScribe").value(_MIC_DEVICE_KEY, None)


def set_last_mic_device_name(name: str) -> None:
    QSettings("MeetingScribe", "MeetingScribe").setValue(_MIC_DEVICE_KEY, name)


def get_last_loopback_device_name():
    return QSettings("MeetingScribe", "MeetingScribe").value(_LOOPBACK_DEVICE_KEY, None)


def set_last_loopback_device_name(name: str) -> None:
    QSettings("MeetingScribe", "MeetingScribe").setValue(_LOOPBACK_DEVICE_KEY, name)


def get_exclusive_default() -> bool:
    return QSettings("MeetingScribe", "MeetingScribe").value(_EXCLUSIVE_KEY, False, type=bool)


def set_exclusive_default(value: bool) -> None:
    QSettings("MeetingScribe", "MeetingScribe").setValue(_EXCLUSIVE_KEY, value)
