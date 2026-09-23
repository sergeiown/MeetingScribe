"""Writes the final transcript to output/, plus a per-file segments/
diarization-status sidecar under the internal .segments/ (not output/ itself -
see paths.SEGMENTS_DIR) so a later "identify speakers" pass can skip
re-transcribing."""

import json
from pathlib import Path

from .paths import OUTPUT_DIR, SEGMENTS_DIR


def save_result(file_path: Path, text: str) -> Path:
    OUTPUT_DIR.mkdir(exist_ok=True)
    out = OUTPUT_DIR / (file_path.stem + ".txt")
    out.write_text(text, encoding="utf-8")
    return out


def rename_input_file(old_path: Path, new_stem: str) -> Path:
    """Renames an input file in place, keeping its extension, and renames
    its matching output transcript / segments sidecar (if any) to match -
    both are paired to the input file by stem, so leaving them behind would
    silently break that pairing."""
    new_stem = new_stem.strip()
    if not new_stem:
        raise ValueError("Name cannot be empty.")
    new_path = old_path.with_name(new_stem + old_path.suffix)
    if new_path != old_path and new_path.exists():
        raise FileExistsError(f'A file named "{new_path.name}" already exists.')
    old_txt = OUTPUT_DIR / (old_path.stem + ".txt")
    old_segments = _segments_sidecar_path(old_path)
    old_path.rename(new_path)
    if old_txt.exists():
        old_txt.rename(OUTPUT_DIR / (new_stem + ".txt"))
    if old_segments.exists():
        old_segments.rename(_segments_sidecar_path(new_path))
    return new_path


def _segments_sidecar_path(file_path: Path) -> Path:
    return SEGMENTS_DIR / (file_path.stem + ".segments.json")


def save_segments(file_path: Path, segments, *, diarized: bool) -> None:
    SEGMENTS_DIR.mkdir(exist_ok=True)
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
