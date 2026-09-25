"""QApplication bootstrap: logging, workdirs, first-run model check, MainWindow."""

import shutil
import sys
import threading
import time

import core

from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox, QSplashScreen

from .bootstrap import dependencies_installed, ensure_dependencies
from .i18n import tr
from .main_window import MainWindow
from .single_instance import acquire_lock, notify_running_instance
from .style import apply_theme, get_theme_preference, get_show_splash_preference

_SPLASH_WIDTH = 640
_SPLASH_MIN_SECONDS = 3.0


def _seed_demo_input():
    """Copy the bundled samples/demo.wav into input/ once, on the very first
    launch, so there's something ready to try - gated on a persistent flag
    rather than "input/ is currently empty", so deleting the demo later
    doesn't bring it back on the next launch."""
    settings = QSettings("MeetingScribe", "MeetingScribe")
    if settings.value("demo_input_seeded", False, type=bool):
        return
    settings.setValue("demo_input_seeded", True)
    has_media = any(
        f.is_file() and f.suffix.lower() in core.SUPPORTED_EXTENSIONS
        for f in core.INPUT_DIR.iterdir()
    )
    if has_media:
        return
    demo = core.SAMPLES_DIR / "demo.wav"
    if demo.exists():
        shutil.copy(str(demo), str(core.INPUT_DIR / demo.name))


def _seed_demo_speaker():
    """Copy the bundled samples/demo_speaker.npy into speakers/ once, on the
    very first launch - same persistent-flag reasoning as _seed_demo_input."""
    settings = QSettings("MeetingScribe", "MeetingScribe")
    if settings.value("demo_speaker_seeded", False, type=bool):
        return
    settings.setValue("demo_speaker_seeded", True)
    if any(core.SPEAKERS_DIR.glob("*.npy")):
        return
    demo_speaker = core.SAMPLES_DIR / "demo_speaker.npy"
    if demo_speaker.exists():
        core.SPEAKERS_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy(str(demo_speaker), str(core.SPEAKERS_DIR / "Demo Speaker (v1).npy"))


def _set_windows_app_id():
    """Sets the process AppUserModelID so the Windows taskbar uses our icon
    instead of pythonw.exe's; must run before any window is created."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MeetingScribe.MeetingScribe")
    except Exception:
        pass


def main():
    _set_windows_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName("MeetingScribe")
    if not acquire_lock():
        # Bring the already-running instance's window to front (it may be
        # sitting minimized to the tray, invisible, which is exactly the
        # confusing case this is for) instead of just telling the user it's
        # already running and leaving them to go find it themselves. Falls
        # back to the plain message only if that instance's window couldn't
        # be found at all (e.g. it's still starting up).
        if not notify_running_instance():
            QMessageBox.warning(None, tr("MeetingScribe"), tr("MeetingScribe is already running."))
        sys.exit(0)
    icon_path = core.SCRIPT_DIR / "img" / "icon.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    apply_theme()

    # Follow the OS theme live, unless the user overrode it (Settings > General).
    def _on_system_theme_changed(_scheme):
        if get_theme_preference() == "auto":
            apply_theme()

    app.styleHints().colorSchemeChanged.connect(_on_system_theme_changed)

    core.ensure_workdirs()
    core.cleanup_update_downloads()
    core.init_logger()

    # Pre-warms core.detect_hardware_info()'s cache in the background so
    # it's already done by the time Settings is first opened - confirmed by
    # direct timing that on a machine with no NVIDIA GPU, its WMI fallback
    # (a whole spawned PowerShell process) alone took ~1.9s, which was
    # nearly the entire delay in opening the dialog. Thread-safe: it's pure
    # subprocess calls, no Qt/GUI object touched from this thread.
    threading.Thread(target=core.detect_hardware_info, daemon=True).start()

    # The mandatory model installs unconditionally like pip deps, no confirmation
    # dialog; both share one combined progress window rather than separate ones.
    model_to_bundle = None
    if not core.installed_whisper_sizes():
        model_to_bundle = next(s for s in core.WHISPER_MODELS if s.mandatory)
    needs_bootstrap = model_to_bundle is not None or not dependencies_installed()

    # Fills the otherwise-blank stretch before MainWindow is ready to show.
    # Skipped ahead of a first-run bootstrap: that flow has its own, much
    # longer-running progress dialog, and the two would just overlap.
    splash = None
    splash_shown_at = None
    if get_show_splash_preference() and not needs_bootstrap:
        splash_path = core.SCRIPT_DIR / "img" / "meetingscribe_cover.png"
        if splash_path.exists():
            pixmap = QPixmap(str(splash_path))
            pixmap = pixmap.scaledToWidth(_SPLASH_WIDTH, Qt.SmoothTransformation)
            splash = QSplashScreen(pixmap)
            splash.show()
            app.processEvents()
            splash_shown_at = time.monotonic()

    ok, model_error = ensure_dependencies(
        core.SCRIPT_DIR / "requirements.txt",
        model_spec=model_to_bundle, hf_token=core.read_hf_token())
    if not ok:
        sys.exit(1)
    if model_error:
        QMessageBox.warning(
            None, tr("Download failed"),
            tr("Could not download the model: {error}\n\nYou can retry later from Settings > Models.",
               error=model_error))

    _seed_demo_input()
    _seed_demo_speaker()

    window = MainWindow()
    if splash is not None:
        # Guarantees the splash is actually visible for a bit, even when
        # startup is fast enough that it would otherwise flash by unread.
        while time.monotonic() - splash_shown_at < _SPLASH_MIN_SECONDS:
            app.processEvents()
            time.sleep(0.05)
        splash.finish(window)
    window.show()
    sys.exit(app.exec())
