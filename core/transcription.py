"""Audio-file transcription via faster-whisper."""

import logging
import shutil
from collections import namedtuple
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .audio import convert_to_wav, format_time, get_duration
from . import whisper_cpp
from .cancellation import Cancelled, CancelToken, ProgressFn, StatusFn
from .device import detect_device, detect_hardware_info, nvidia_gpu_present
from .paths import MODELS_DIR

_log = logging.getLogger(__name__)

# Stand-in for faster-whisper's Segment when reloading from a saved sidecar file; only start/end/text are used downstream.
Segment = namedtuple("Segment", "start end text")


@dataclass
class TranscribeResult:
    segments: list
    text: str
    wav_path: str
    tmp_dir: str
    device: str
    compute_type: str
    device_name: str
    detected_language: str
    language_probability: float


def _configured_cpu_threads() -> int:
    """CPU_THREADS in config.env; 0 (default, or anything invalid) lets the library decide."""
    from .config_env import read_config_env
    try:
        return max(0, int(read_config_env().get("CPU_THREADS", "0")))
    except ValueError:
        return 0


def _configured_engine() -> str:
    """ENGINE in config.env: "auto" (default), "vulkan" or "faster-whisper"."""
    from .config_env import read_config_env
    value = read_config_env().get("ENGINE", "auto").strip().lower()
    return value if value in ("auto", "vulkan", "faster-whisper") else "auto"


def choose_engine(device_preference: str = "auto") -> str:
    """"vulkan" (whisper.cpp on an AMD/Intel GPU) or "faster-whisper" (CUDA/CPU).
    An NVIDIA GPU keeps faster-whisper: CUDA is faster there."""
    setting = _configured_engine()
    if setting == "faster-whisper" or not whisper_cpp.is_available():
        return "faster-whisper"
    if setting == "vulkan":
        return "vulkan"
    if device_preference == "cpu" or nvidia_gpu_present():
        return "faster-whisper"
    gpu_name = detect_hardware_info()[1]
    return "vulkan" if whisper_cpp.gpu_is_usable(gpu_name) else "faster-whisper"


def recommended_model() -> tuple:
    """(engine, model key) worth downloading first on this system: large-v3 on
    an NVIDIA or Vulkan-capable GPU, the light small model on a CPU-only machine."""
    if choose_engine("auto") == "vulkan":
        return "vulkan", "large-v3"
    if nvidia_gpu_present():
        return "faster-whisper", "large-v3"
    return "faster-whisper", "small"


def _assemble_text(segments) -> str:
    lines = []
    last_ts = -30
    for seg in segments:
        if seg.start - last_ts >= 30:
            lines.append(f"\n{format_time(seg.start)}")
            last_ts = seg.start
        text = seg.text.strip()
        if text:
            lines.append(text)
    return "\n".join(lines).strip()


def _transcribe_vulkan(file_path, model_size, language, total_dur, models_dir, cancel_token,
                       on_status, on_progress) -> TranscribeResult:
    model_path = whisper_cpp.ggml_model_path(model_size, models_dir)
    gpu_name = detect_hardware_info()[1] or "GPU"
    if on_status:
        on_status(f"Converting to WAV... [{gpu_name}]")
    wav, tmp_dir = convert_to_wav(file_path)
    try:
        dur = total_dur if (total_dur and total_dur > 0) else get_duration(wav)
        if on_status:
            on_status("Transcribing...")
        segments, lang, prob = whisper_cpp.transcribe(
            wav, model_path, language, dur, models_dir, cancel_token, on_progress)
        if on_progress:
            on_progress(dur, dur, "")
        _log.info("Transcription (Vulkan): lang=%s segments=%d", lang, len(segments))
        return TranscribeResult(
            segments=segments, text=_assemble_text(segments), wav_path=wav, tmp_dir=tmp_dir,
            device="vulkan", compute_type="ggml", device_name=gpu_name,
            detected_language=lang, language_probability=prob)
    except BaseException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


def transcribe_file(file_path: Path, model_size: str, language: Optional[str], total_dur: float, *,
                     models_dir: Path = MODELS_DIR,
                     device_preference: str = "auto",
                     cancel_token: Optional[CancelToken] = None,
                     on_status: Optional[StatusFn] = None,
                     on_progress: Optional[ProgressFn] = None) -> TranscribeResult:
    cancel_token = cancel_token or CancelToken()

    engine = choose_engine(device_preference)
    if engine == "vulkan" and not whisper_cpp.is_model_installed(model_size, models_dir):
        _log.info("GPU model %s not downloaded, using faster-whisper", model_size)
        if on_status:
            on_status("GPU model not downloaded (Settings > Models), using the CPU...")
        engine = "faster-whisper"
    if engine == "vulkan":
        try:
            return _transcribe_vulkan(file_path, model_size, language, total_dur, models_dir,
                                      cancel_token, on_status, on_progress)
        except Cancelled:
            raise
        except Exception as e:
            _log.warning("Vulkan engine failed, falling back to faster-whisper: %s", e)
            if on_status:
                on_status("GPU engine failed, using faster-whisper...")

    from faster_whisper import WhisperModel

    model_path_local = models_dir / model_size
    model_path = (str(model_path_local)
                  if model_path_local.exists() and any(model_path_local.iterdir())
                  else model_size)

    device, compute_type, device_name = detect_device(prefer=device_preference)
    if on_status:
        on_status(f"Loading model {model_size}...")
    cpu_threads = _configured_cpu_threads() if device == "cpu" else 0
    model = WhisperModel(model_path, device=device, compute_type=compute_type,
                         cpu_threads=cpu_threads, download_root=str(model_path_local))
    _log.debug("Model loaded: device=%s compute_type=%s", device, compute_type)
    if on_status:
        on_status(f"Model loaded [{device_name}]")

    if on_status:
        on_status("Converting to WAV...")
    wav, tmp_dir = convert_to_wav(file_path)
    try:
        # Duration can be 0 if ffprobe couldn't read the original path; recompute from the ASCII copy.
        dur = total_dur if (total_dur and total_dur > 0) else get_duration(wav)

        if on_status:
            on_status("Transcribing...")

        segments_gen, info = model.transcribe(
            wav,
            language=language,
            beam_size=5,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
            word_timestamps=False,
            # No temperature fallback / context carry-over: on CPU they cost 4-5x and made timing erratic
            temperature=0.0,
            condition_on_previous_text=False,
        )

        results = []
        last_ts = -30
        lines = []

        for seg in segments_gen:
            cancel_token.check()
            results.append(seg)
            if on_progress:
                on_progress(seg.end, dur, seg.text.strip())
            if seg.start - last_ts >= 30:
                lines.append(f"\n{format_time(seg.start)}")
                last_ts = seg.start
            text = seg.text.strip()
            if text:
                lines.append(text)

        if on_progress:
            on_progress(dur, dur, "")  # label empty: not real transcript content, bar-fill only

        _log.info("Transcription: lang=%s confidence=%.0f%% segments=%d",
                  info.language, info.language_probability * 100, len(results))

        return TranscribeResult(
            segments=results,
            text="\n".join(lines).strip(),
            wav_path=wav,
            tmp_dir=tmp_dir,
            device=device,
            compute_type=compute_type,
            device_name=device_name,
            detected_language=info.language,
            language_probability=info.language_probability,
        )
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
