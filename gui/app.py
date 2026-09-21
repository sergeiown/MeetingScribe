"""QApplication bootstrap: logging, workdirs, first-run model check, MainWindow."""

import shutil
import sys

import core

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox, QSplashScreen

from .bootstrap import dependencies_installed, ensure_dependencies
from .i18n import tr
from .main_window import MainWindow
from .style import apply_theme, get_theme_preference, get_show_splash_preference

_SPLASH_WIDTH = 640


def _seed_demo_input():
    """Copy the bundled samples/demo.wav into input/ if it has no media yet,
    so a fresh checkout has something ready to try."""
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
    """Copy the bundled samples/demo_speaker.npy into speakers/ if empty, so
    the demo recording has one speaker pre-identified and one not."""
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
    if get_show_splash_preference() and not needs_bootstrap:
        splash_path = core.SCRIPT_DIR / "img" / "meetingscribe_cover.png"
        if splash_path.exists():
            pixmap = QPixmap(str(splash_path))
            pixmap = pixmap.scaledToWidth(_SPLASH_WIDTH, Qt.SmoothTransformation)
            splash = QSplashScreen(pixmap)
            splash.show()
            app.processEvents()

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
        splash.finish(window)
    window.show()
    sys.exit(app.exec())
