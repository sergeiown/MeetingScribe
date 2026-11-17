"""Speech recognition through whisper.cpp's Vulkan build, for AMD/Intel GPUs
that faster-whisper (CUDA/CPU only) cannot use. Runs the bundled
whisper-cli.exe as a child process and parses the timestamped lines it prints."""

import logging
import os
import queue
import re
import subprocess
import threading
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


def delete_model(size: str, models_dir: Path = MODELS_DIR) -> None:
    try:
        ggml_model_path(size, models_dir).unlink()
    except OSError:
        pass


def _download(url: str, dest: Path, cancel_token: CancelToken,
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


def ensure_model(size: str, models_dir: Path, cancel_token: CancelToken,
                 on_progress: Optional[ProgressCb] = None) -> Path:
    path = ggml_model_path(size, models_dir)
    if not is_model_installed(size, models_dir):
        _download(_HF_MODELS + f"ggml-{size}.bin", path, cancel_token, on_progress)
    return path


def _ensure_vad(models_dir: Path, cancel_token: CancelToken) -> Optional[Path]:
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
               on_progress: Optional[Callable[[float, float, str], None]] = None):
    """-> (segments, detected_language, language_probability). Raises
    EngineError if the engine fails; Cancelled if cancelled (child is killed)."""
    cmd = [str(ENGINE_EXE), "-m", str(model_path), "-f", str(wav_path),
           "-l", language or "auto", "-pp"]
    vad = _ensure_vad(models_dir, cancel_token)
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
            m = _PROGRESS.search(line)
            if m and on_progress and total_dur:
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
