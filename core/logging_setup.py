"""Session logging, explicitly initialized (not an import-time side effect).

core.* modules log via logging.getLogger(__name__) (e.g. "core.diarization"),
which is a child of the "core" logger configured here and propagates to its
file handler - no extra wiring needed per module.
"""

import logging
from datetime import datetime

from ._util import silence
from .paths import LOGS_DIR

_ROOT_LOGGER_NAME = "core"


def init_logger():
    """Create/reset logs/session.log and return (logger, log_path).

    Safe to call from any process, including the main GUI process - does
    NOT import torch or pyannote.audio (see log_ml_versions for that): those
    pull in matplotlib, which auto-registers a Qt backend that conflicts
    with PySide6 when both are loaded in the same process (this is exactly
    why the ML pipeline runs in its own OS process - see
    gui/process_worker.py's module docstring). faster-whisper is safe here,
    it doesn't pull in matplotlib.
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / "session.log"
    logger = logging.getLogger(_ROOT_LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    fh = logging.FileHandler(log_path, encoding="utf-8", mode="w")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-7s  %(name)s  %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(fh)
    logger.info("=== Session started %s ===", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    try:
        import faster_whisper
        logger.debug("faster-whisper %s", faster_whisper.__version__)
    except Exception:
        pass
    return logger, log_path


def log_ml_versions():
    """Logs torch/pyannote.audio versions. Only call this from the
    process-isolated ML pipeline (gui/process_worker.py) - never from the
    main GUI process (see init_logger's docstring for why)."""
    logger = logging.getLogger(_ROOT_LOGGER_NAME)
    try:
        import torch
        logger.debug("torch %s", torch.__version__)
        # must run AFTER torch import: torch._logging resets "torch" logger to WARNING
        logging.getLogger("torch").setLevel(logging.ERROR)
    except Exception:
        pass
    try:
        with silence():
            import pyannote.audio
        logger.debug("pyannote.audio %s", pyannote.audio.__version__)
    except Exception:
        pass
