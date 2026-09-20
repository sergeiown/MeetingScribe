"""Writing the final transcript to output/, and a sidecar recording the raw
transcript segments plus whether they've been diarized yet - so a later,
separate "identify speakers" pass can run without re-transcribing."""

import json
from pathlib import Path

from .paths import OUTPUT_DIR


def save_result(file_path: Path, text: str) -> Path:
    OUTPUT_DIR.mkdir(exist_ok=True)
    out = OUTPUT_DIR / (file_path.stem + ".txt")
    out.write_text(text, encoding="utf-8")
    return out


def _segments_sidecar_path(file_path: Path) -> Path:
    return OUTPUT_DIR / (file_path.stem + ".segments.json")


def save_segments(file_path: Path, segments, *, diarized: bool) -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    data = {
        "diarized": diarized,
        "segments": [{"start": s.start, "end": s.end, "text": s.text} for s in segments],
    }
    _segments_sidecar_path(file_path).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def load_segments(file_path: Path):
    path = _segments_sidecar_path(file_path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def file_status(file_path: Path):
    """(transcribed, diarizable) for one input file. diarizable means a
    segments sidecar exists and hasn't been diarized yet - i.e. speaker
    identification can run on it without re-running speech recognition."""
    transcribed = (OUTPUT_DIR / (file_path.stem + ".txt")).exists()
    data = load_segments(file_path)
    diarizable = bool(data) and not data.get("diarized", False)
    return transcribed, diarizable
