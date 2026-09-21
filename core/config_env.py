"""Read/write config.env (HF_TOKEN); diarization-library availability checks."""

import importlib.util
import os
from pathlib import Path

from .paths import SCRIPT_DIR, MODELS_DIR

CONFIG_ENV_PATH = SCRIPT_DIR / "config.env"
CONFIG_ENV_EXAMPLE_PATH = SCRIPT_DIR / "config.env.example"

_PLACEHOLDER_TOKEN = "hf_YOUR_TOKEN_HERE"


def read_config_env(path: Path = CONFIG_ENV_PATH) -> dict:
    """{} if the file doesn't exist; ignores comment/blank lines."""
    if not path.exists():
        return {}
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip()
    return result


def read_hf_token(path: Path = CONFIG_ENV_PATH) -> str:
    """os.environ['HF_TOKEN'] wins if set; else config.env's HF_TOKEN.
    The config.env.example placeholder value counts as empty."""
    token = (os.environ.get("HF_TOKEN") or read_config_env(path).get("HF_TOKEN", "")).strip()
    return "" if token == _PLACEHOLDER_TOKEN else token


def write_config_env(updates: dict, path: Path = CONFIG_ENV_PATH) -> None:
    """Preserves existing lines verbatim except updated keys (in place;
    unmatched keys appended); seeds from config.env.example if `path` is
    missing. Writes via temp file + os.replace so a crash can't corrupt it."""
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    elif CONFIG_ENV_EXAMPLE_PATH.exists():
        lines = CONFIG_ENV_EXAMPLE_PATH.read_text(encoding="utf-8").splitlines()
    else:
        lines = []

    remaining = dict(updates)
    out_lines = []
    for line in lines:
        stripped = line.strip()
        if "=" in stripped and not stripped.startswith("#"):
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                out_lines.append(f"{key}={remaining.pop(key)}")
                continue
        out_lines.append(line)
    for key, value in remaining.items():
        out_lines.append(f"{key}={value}")

    content = "\n".join(out_lines) + "\n"
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    os.replace(tmp_path, path)


def has_diarization_support() -> bool:
    """Presence check only (find_spec, not a real import) - a real import
    here would reintroduce the matplotlib/Qt conflict process isolation
    exists to avoid (see gui/process_worker.py)."""
    return (importlib.util.find_spec("torch") is not None
            and importlib.util.find_spec("pyannote.audio") is not None)


def is_diar_pipeline_cached(models_dir: Path = MODELS_DIR) -> bool:
    """True if the pyannote diarization pipeline is present locally (offline-ready)."""
    base = models_dir / "models--pyannote--speaker-diarization-3.1"
    if not base.exists():
        return False
    for f in base.rglob("config.yaml"):
        if f.is_file() and f.stat().st_size > 64:
            return True
    return False
