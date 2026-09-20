"""ffmpeg/ffprobe discovery, time/duration formatting, and WAV conversion."""

import logging
import os
import shutil
import subprocess
import tempfile

_log = logging.getLogger(__name__)


class MediaError(Exception):
    """Input file could not be read or converted (corrupted, incomplete, or unsupported)."""


def find_ffmpeg():
    if shutil.which("ffprobe"):
        return "ffprobe", "ffmpeg"
    for base in [
        r"C:\ffmpeg\bin",
        r"C:\Program Files\ffmpeg\bin",
        r"C:\Program Files (x86)\ffmpeg\bin",
    ]:
        fp = os.path.join(base, "ffprobe.exe")
        ff = os.path.join(base, "ffmpeg.exe")
        if os.path.exists(fp):
            return fp, ff
    return None, None


FFPROBE, FFMPEG = find_ffmpeg()


def get_duration(path):
    if not FFPROBE:
        return 0
    try:
        result = subprocess.run(
            [FFPROBE, "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True
        )
        return float(result.stdout.strip())
    except Exception:
        return 0


def format_duration(secs):
    h, r = divmod(int(secs), 3600)
    m, s = divmod(r, 60)
    if h > 0:
        return f"{h}h {m:02d}m {s:02d}s"
    elif m > 0:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def convert_to_wav(file_path):
    """Converts file_path to a 16kHz mono WAV in a fresh temp dir. Returns
    (wav_path, tmp_dir); the caller owns cleanup of tmp_dir.

    ffmpeg on Windows can fail to open files with non-ASCII names (e.g.
    Cyrillic), so it's run against an ASCII-named hardlink/copy instead."""
    tmp_dir = tempfile.mkdtemp()
    try:
        src = os.path.join(tmp_dir, "source" + file_path.suffix.lower())
        try:
            os.link(str(file_path), src)  # instant; no extra space (same volume)
        except OSError:
            shutil.copyfile(str(file_path), src)  # fallback (e.g. across volumes)
        wav = os.path.join(tmp_dir, "audio.wav")
        result = subprocess.run(
            [FFMPEG or "ffmpeg", "-y", "-i", src, "-ar", "16000", "-ac", "1", "-f", "wav", wav],
            capture_output=True
        )
        if result.returncode != 0 or not os.path.exists(wav):
            tail = (result.stderr or b"").decode("utf-8", "replace").strip().splitlines()
            detail = "  ".join(tail[-3:]) if tail else "unknown ffmpeg error"
            _log.warning("ffmpeg could not convert %s: %s", file_path.name, detail)
            low = detail.lower()
            if any(s in low for s in ("invalid data", "moov atom", "error opening input",
                                      "end of file", "could not find codec", "does not contain")):
                raise MediaError("file looks corrupted, incomplete, or not a valid media file")
            raise MediaError("ffmpeg could not read this file")
        return wav, tmp_dir
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


def format_time(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"
