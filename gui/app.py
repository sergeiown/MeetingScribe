"""QApplication bootstrap: logging, workdirs, first-run model check, MainWindow."""

import shutil
import sys

import core

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from .bootstrap import ensure_dependencies
from .i18n import tr
from .main_window import MainWindow
from .style import apply_theme, get_theme_preference


def _seed_demo_input():
    """Copy the bundled samples/demo.wav into input/ if input/ has no media
    file yet, so a fresh checkout always has something ready to try - a
    local file copy, not a network action, so it doesn't fall under the
    "no silent downloads" policy."""
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
    """Copy the bundled samples/demo_speaker.npy into speakers/ if speakers/
    is empty, so the demo recording (two speakers) can show off both halves
    of speaker identification right away: one voice already known by name,
    the other deliberately left unrecognized until the user names it."""
    if any(core.SPEAKERS_DIR.glob("*.npy")):
        return
    demo_speaker = core.SAMPLES_DIR / "demo_speaker.npy"
    if demo_speaker.exists():
        core.SPEAKERS_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy(str(demo_speaker), str(core.SPEAKERS_DIR / "Demo Speaker (v1).npy"))


def _set_windows_app_id():
    """Without this, Windows attributes the taskbar button to whichever exe
    is actually running the process (pythonw.exe here, launched by the tiny
    launcher.exe) and shows ITS icon there instead of ours, regardless of
    setWindowIcon() below - the taskbar groups/icons by this id, not by the
    window icon alone. Must run before any window is created."""
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

    # Follow the OS theme live, but only while the user hasn't overridden it
    # in Settings > General (theme preference "auto" is the default).
    def _on_system_theme_changed(_scheme):
        if get_theme_preference() == "auto":
            apply_theme()

    app.styleHints().colorSchemeChanged.connect(_on_system_theme_changed)

    core.ensure_workdirs()
    core.init_logger()

    # The mandatory model is exactly that - mandatory - so it's bundled in
    # unconditionally, no confirmation dialog, the same way pip deps never
    # ask either. Run whichever of {pip deps, the model} is actually needed
    # through ONE combined progress window - never a components window
    # followed by a separate model-download window.
    model_to_bundle = None
    if not core.installed_whisper_sizes():
        model_to_bundle = next(s for s in core.WHISPER_MODELS if s.mandatory)

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
    window.show()
    sys.exit(app.exec())
