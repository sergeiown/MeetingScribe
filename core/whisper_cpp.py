"""Speech recognition through whisper.cpp's Vulkan build, for AMD/Intel GPUs
that faster-whisper (CUDA/CPU only) cannot use. Runs the bundled
whisper-cli.exe as a child process and parses the timestamped lines it prints."""

import logging
import os
import queue
import re
import subprocess
import http.client
import threading
import time
import urllib.error
import urllib.request
from collections import namedtuple
from pathlib import Path
from typing import Callable, Optional

from .cancellation import Cancelled, CancelToken
from .paths import MODELS_DIR, SCRIPT_DIR

_log = logging.getLogger(__name__)

ENGINE_DIR = SCRIPT_DIR / "bin" / "whisper-vulkan"
ENGINE_EXE = ENGINE_DIR / "whisper-cli.exe"

_HF_MODELS = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"
_VAD_URL = "https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v5.1.2.bin"
_VAD_FILE = "ggml-silero-v5.1.2.bin"
_MIN_MODEL_BYTES = 10 * 1024 * 1024

GGML_SIZES = {"large-v3": "~3.1 GB", "large-v3-turbo": "~1.6 GB", "small": "~490 MB"}
GGML_BYTES = {"large-v3": 3095033483, "large-v3-turbo": 1624555275, "small": 487601967}

_GPU_VENDORS = re.compile(r"amd|radeon|intel|arc\b", re.IGNORECASE)
_SEGMENT = re.compile(r"^\[(\d+):(\d+):(\d+(?:\.\d+)?) --> (\d+):(\d+):(\d+(?:\.\d+)?)\]\s*(.*)$")
_PROGRESS = re.compile(r"progress\s*=\s*(\d+)%")
_DETECTED = re.compile(r"auto-detected language:\s*(\w+)\s*\(p\s*=\s*([\d.]+)\)")

Segment = namedtuple("Segment", "start end text")

ProgressCb = Callable[[int, int], None]  # (bytes_done, bytes_total)


class EngineError(Exception):
    pass


def is_available() -> bool:
    return ENGINE_EXE.exists()


def gpu_is_usable(gpu_name: Optional[str]) -> bool:
    return bool(gpu_name and _GPU_VENDORS.search(gpu_name))


def ggml_dir(models_dir: Path = MODELS_DIR) -> Path:
    return models_dir / "ggml"


def ggml_model_path(size: str, models_dir: Path = MODELS_DIR) -> Path:
    return ggml_dir(models_dir) / f"ggml-{size}.bin"


def is_model_installed(size: str, models_dir: Path = MODELS_DIR) -> bool:
    p = ggml_model_path(size, models_dir)
    return p.exists() and p.stat().st_size > _MIN_MODEL_BYTES


def needs_first_run_model(size: str, models_dir: Path = MODELS_DIR) -> bool:
    """True only before GPU models were ever offered (the ggml folder doesn't
    exist yet), so a model the user deleted on purpose isn't fetched again."""
    return not ggml_dir(models_dir).exists() and not is_model_installed(size, models_dir)


def delete_model(size: str, models_dir: Path = MODELS_DIR) -> None:
    path = ggml_model_path(size, models_dir)
    for p in (path, path.with_name(path.name + ".part")):
        try:
            p.unlink()
        except OSError:
            pass


def _download_once(url: str, dest: Path, cancel_token: CancelToken,
                   on_progress: Optional[ProgressCb] = None) -> None:
    """Resumable: keeps a .part file and continues it with a Range request."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    done = part.stat().st_size if part.exists() else 0
    req = urllib.request.Request(url, headers={"User-Agent": "MeetingScribe"})
    if done:
        req.add_header("Range", f"bytes={done}-")
    with urllib.request.urlopen(req, timeout=60) as resp:
        if done and resp.status != 206:
            done = 0
        total = done + int(resp.headers.get("Content-Length") or 0)
        with open(part, "ab" if done else "wb") as f:
            while True:
                cancel_token.check()
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if on_progress and total:
                    on_progress(done, total)
    if total and done < total:
        raise EngineError("download interrupted")
    os.replace(part, dest)


_MAX_ATTEMPTS = 8


def _is_transient(e: Exception) -> bool:
    if isinstance(e, urllib.error.HTTPError):
        return e.code >= 500 or e.code == 429
    return isinstance(e, (OSError, http.client.HTTPException, EngineError))


def _download(url: str, dest: Path, cancel_token: CancelToken,
              on_progress: Optional[ProgressCb] = None) -> None:
    """A dropped connection (common on multi-GB transfers) resumes from the
    .part file instead of failing the whole download."""
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            _download_once(url, dest, cancel_token, on_progress)
            return
        except Cancelled:
            raise
        except Exception as e:
            if attempt == _MAX_ATTEMPTS or not _is_transient(e):
                raise
            _log.warning("Download interrupted (%s), retrying %d/%d", e, attempt, _MAX_ATTEMPTS - 1)
            for _ in range(attempt * 4):
                cancel_token.check()
                time.sleep(0.5)


def ensure_model(size: str, models_dir: Path, cancel_token: CancelToken,
                 on_progress: Optional[ProgressCb] = None) -> Path:
    path = ggml_model_path(size, models_dir)
    if not is_model_installed(size, models_dir):
        _download(_HF_MODELS + f"ggml-{size}.bin", path, cancel_token, on_progress)
    return path


def ensure_vad(models_dir: Path, cancel_token: CancelToken) -> Optional[Path]:
    path = ggml_dir(models_dir) / _VAD_FILE
    if path.exists() and path.stat().st_size > 100 * 1024:
        return path
    try:
        _download(_VAD_URL, path, cancel_token)
        return path
    except Cancelled:
        raise
    except Exception as e:
        _log.warning("VAD model unavailable, continuing without it: %s", e)
        return None


def _seconds(h: str, m: str, s: str) -> float:
    return int(h) * 3600 + int(m) * 60 + float(s)


def transcribe(wav_path: str, model_path: Path, language: Optional[str], total_dur: float,
               models_dir: Path, cancel_token: CancelToken,
               on_progress: Optional[Callable[[float, float, str], None]] = None,
               on_status: Optional[Callable[[str], None]] = None):
    """-> (segments, detected_language, language_probability). Raises
    EngineError if the engine fails; Cancelled if cancelled (child is killed)."""
    cmd = [str(ENGINE_EXE), "-m", str(model_path), "-f", str(wav_path),
           "-l", language or "auto", "-pp"]
    vad = ensure_vad(models_dir, cancel_token)
    if vad is not None:
        cmd += ["--vad", "-vm", str(vad)]

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
        cwd=str(ENGINE_DIR), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    out_lines: "queue.Queue[Optional[bytes]]" = queue.Queue()
    err_tail = []
    state = {"lang": language or "", "prob": 1.0 if language else 0.0, "gpu": False}

    def read_out():
        for raw in proc.stdout:
            out_lines.put(raw)
        out_lines.put(None)

    def read_err():
        for raw in proc.stderr:
            line = raw.decode("utf-8", "replace").rstrip()
            err_tail.append(line)
            del err_tail[:-30]
            if "using Vulkan" in line:
                state["gpu"] = True
            m = _DETECTED.search(line)
            if m and not language:
                state["lang"], state["prob"] = m.group(1), float(m.group(2))
            if on_status:
                if "VAD is enabled" in line and not state.get("vad"):
                    state["vad"] = True
                    on_status("Detecting speech...")
                speech_search_done = "Reduced audio" in line or ("main: processing" in line and vad is None)
                if speech_search_done and not state.get("started"):
                    state["started"] = True
                    on_status("Transcribing...")
            m = _PROGRESS.search(line)
            if m:
                if on_status and not state.get("started"):
                    state["started"] = True
                    on_status("Transcribing...")
                if on_progress and total_dur:
                    on_progress(total_dur * int(m.group(1)) / 100.0, total_dur, "")

    threads = [threading.Thread(target=read_out, daemon=True), threading.Thread(target=read_err, daemon=True)]
    for t in threads:
        t.start()

    segments = []
    try:
        while True:
            if cancel_token.is_cancelled():
                proc.kill()
                cancel_token.check()
            try:
                raw = out_lines.get(timeout=0.2)
            except queue.Empty:
                continue
            if raw is None:
                break
            m = _SEGMENT.match(raw.decode("utf-8", "replace").strip())
            if not m:
                continue
            start, end = _seconds(*m.group(1, 2, 3)), _seconds(*m.group(4, 5, 6))
            text = m.group(7).strip()
            segments.append(Segment(start, end, text))
            if on_progress and total_dur:
                on_progress(end, total_dur, text)
        proc.wait()
    except BaseException:
        if proc.poll() is None:
            proc.kill()
        raise
    finally:
        for t in threads:
            t.join(timeout=2)

    if proc.returncode != 0:
        raise EngineError(f"whisper-cli exited with {proc.returncode}: " + " | ".join(err_tail[-5:]))
    if not state["gpu"]:
        _log.warning("whisper.cpp did not report a Vulkan backend; it ran on the CPU")
    return segments, state["lang"], state["prob"]
