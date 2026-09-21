"""Filesystem layout shared across the app."""

from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent
INPUT_DIR = SCRIPT_DIR / "input"
OUTPUT_DIR = SCRIPT_DIR / "output"
MODELS_DIR = SCRIPT_DIR / "models"
LOGS_DIR = SCRIPT_DIR / "logs"
SPEAKERS_DIR = SCRIPT_DIR / "speakers"
SAMPLES_DIR = SCRIPT_DIR / "samples"

SUPPORTED_EXTENSIONS = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4a", ".mp3", ".wav"}


def ensure_workdirs() -> None:
    """Create the local working folders if missing - all but samples/ are
    git-ignored, so a fresh clone starts without them."""
    for d in (INPUT_DIR, OUTPUT_DIR, MODELS_DIR, SPEAKERS_DIR, LOGS_DIR):
        d.mkdir(parents=True, exist_ok=True)
