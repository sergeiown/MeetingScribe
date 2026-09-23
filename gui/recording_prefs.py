"""Recording preferences (which sources are active, device choice), stored
like device_prefs.py. Device *name* is persisted, not a numeric index -
indices aren't stable across reboots or when USB audio devices are plugged
in a different order. A device name of None/"" means "system default",
resolved fresh at recording time rather than pinned."""

from PySide6.QtCore import QSettings

_USE_MIC_KEY = "recording_use_mic"
_USE_SYSTEM_KEY = "recording_use_system"
_MIC_DEVICE_KEY = "recording_mic_device"
_LOOPBACK_DEVICE_KEY = "recording_loopback_device"
_HOTKEY_KEY = "recording_hotkey"
_DEVICE_PREF_MIGRATED_KEY = "recording_device_pref_migrated_v1"

DEFAULT_HOTKEY = ""  # off by default - opt-in only, set explicitly in Settings


def _migrate_device_prefs_once() -> None:
    """An earlier build had a live device combo directly in the recording
    panel, which saved a specific device name under these same two keys as
    soon as the panel was built - before "System default" existed as a
    concept to explicitly opt out of. Without this, that leftover value
    would silently look like a deliberate Settings choice and override
    "System default" forever, even though the user never chose it through
    the Settings UI that replaced the panel combo."""
    settings = QSettings("MeetingScribe", "MeetingScribe")
    if settings.value(_DEVICE_PREF_MIGRATED_KEY, False, type=bool):
        return
    settings.remove(_MIC_DEVICE_KEY)
    settings.remove(_LOOPBACK_DEVICE_KEY)
    settings.setValue(_DEVICE_PREF_MIGRATED_KEY, True)


_migrate_device_prefs_once()


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


def get_hotkey() -> str:
    """The same key sequence both starts and stops a recording (see
    gui/global_hotkey.py) - one binding to remember, not two."""
    return QSettings("MeetingScribe", "MeetingScribe").value(_HOTKEY_KEY, DEFAULT_HOTKEY, type=str)


def set_hotkey(value: str) -> None:
    QSettings("MeetingScribe", "MeetingScribe").setValue(_HOTKEY_KEY, value)
