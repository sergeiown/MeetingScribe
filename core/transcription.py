"""Audio-file transcription via faster-whisper."""

import logging
import shutil
from collections import namedtuple
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .audio import convert_to_wav, format_time, get_duration
from .cancellation import CancelToken, ProgressFn, StatusFn
from .device import detect_device
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


def transcribe_file(file_path: Path, model_size: str, language: Optional[str], total_dur: float, *,
                     models_dir: Path = MODELS_DIR,
                     device_preference: str = "auto",
                     cancel_token: Optional[CancelToken] = None,
                     on_status: Optional[StatusFn] = None,
                     on_progress: Optional[ProgressFn] = None) -> TranscribeResult:
    from faster_whisper import WhisperModel

    cancel_token = cancel_token or CancelToken()

    model_path_local = models_dir / model_size
    model_path = (str(model_path_local)
                  if model_path_local.exists() and any(model_path_local.iterdir())
                  else model_size)

    device, compute_type, device_name = detect_device(prefer=device_preference)
    if on_status:
        on_status(f"Loading model {model_size}...")
    model = WhisperModel(model_path, device=device, compute_type=compute_type,
                         download_root=str(model_path_local))
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
