"""Compute-device preference (CPU vs GPU), stored like the theme/language
prefs. Only offered as a Settings choice when both a CPU and a GPU are
detected."""

from PySide6.QtCore import QSettings

_DEVICE_KEY = "device_preference"
DEFAULT_PREFERENCE = "auto"


def get_device_preference() -> str:
    """"auto" (use GPU if present, default) or "cpu" (force CPU)."""
    return QSettings("MeetingScribe", "MeetingScribe").value(_DEVICE_KEY, DEFAULT_PREFERENCE)


def set_device_preference(value: str) -> None:
    QSettings("MeetingScribe", "MeetingScribe").setValue(_DEVICE_KEY, value)
