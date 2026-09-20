"""Compute-device preference (CPU vs GPU), stored the same way as the
theme/language prefs. Only meaningful - and only shown as a choice in
Settings - when both a CPU (always present) and a GPU are detected;
otherwise there's nothing to choose."""

from PySide6.QtCore import QSettings

_DEVICE_KEY = "device_preference"
DEFAULT_PREFERENCE = "auto"


def get_device_preference() -> str:
    """"auto" (use the GPU if present - default), or "cpu" (force CPU even
    with a GPU present)."""
    return QSettings("MeetingScribe", "MeetingScribe").value(_DEVICE_KEY, DEFAULT_PREFERENCE)


def set_device_preference(value: str) -> None:
    QSettings("MeetingScribe", "MeetingScribe").setValue(_DEVICE_KEY, value)
