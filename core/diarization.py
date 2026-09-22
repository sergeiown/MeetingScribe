"""Speaker diarization via pyannote.audio."""

import logging
import warnings
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ._util import silence
from .cancellation import CancelToken, ProgressFn, StatusFn
from .device import detect_device
from .paths import MODELS_DIR

_log = logging.getLogger(__name__)


@dataclass
class DiarizeResult:
    turns: list  # (start, end, label)
    audio: "object"  # np.ndarray
    sr: int


def _build_diarize_hook(cancel_token: CancelToken, on_progress: Optional[ProgressFn]):
    """Called by pyannote per pipeline stage; always checks cancellation, even when a stage reports no progress total."""
    def hook(step_name, step_artifact, file=None, total=None, completed=None):
        cancel_token.check()
        if on_progress and total and completed is not None and total > 0:
            on_progress(float(completed), float(total), step_name)
    return hook


def diarize(wav_path: str, hf_token: str, num_speakers: Optional[int] = None, *,
            models_dir: Path = MODELS_DIR,
            device_preference: str = "auto",
            cancel_token: Optional[CancelToken] = None,
            on_status: Optional[StatusFn] = None,
            on_progress: Optional[ProgressFn] = None) -> DiarizeResult:
    import torch
    import numpy as np
    with silence():
        from pyannote.audio import Pipeline

    cancel_token = cancel_token or CancelToken()

    tok_hint = f"{hf_token[:8]}...{hf_token[-4:]}" if len(hf_token) > 12 else "???"
    _log.debug("Diarization: token=%s wav=%s", tok_hint, wav_path)
    _log.info("Diarization: loading pipeline pyannote/speaker-diarization-3.1")
    if on_status:
        on_status("Loading diarization pipeline...")

    models_dir.mkdir(exist_ok=True)
    diar_local = models_dir / "models--pyannote--speaker-diarization-3.1"
    with silence():
        try:
            if diar_local.exists():
                # Offline-first even here: without local_files_only, this still
                # reaches out to the Hub before falling back to the local
                # cache, which can hang for a long time (no visible error - a
                # confirmed real report) on a slow/flaky connection, for a
                # model that's already fully installed and needs no network at all.
                try:
                    pipeline = Pipeline.from_pretrained(
                        str(diar_local), token=hf_token, local_files_only=True)
                except Exception:
                    pipeline = Pipeline.from_pretrained(str(diar_local), token=hf_token)
            else:
                try:
                    pipeline = Pipeline.from_pretrained(
                        "pyannote/speaker-diarization-3.1", token=hf_token,
                        cache_dir=str(models_dir), local_files_only=True)
                except Exception:
                    pipeline = Pipeline.from_pretrained(
                        "pyannote/speaker-diarization-3.1", token=hf_token,
                        cache_dir=str(models_dir))
        except Exception:
            pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1", token=hf_token)

    device, _, _ = detect_device(prefer=device_preference)
    with silence():
        pipeline = pipeline.to(torch.device(device))
    _log.info("Diarization: device=%s", device)

    with wave.open(wav_path, "rb") as wf:
        sr = wf.getframerate()
        n_ch = wf.getnchannels()
        sw = wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())
    dtype = {1: np.int8, 2: np.int16, 4: np.int32}.get(sw, np.int16)
    audio = np.frombuffer(raw, dtype=dtype).astype(np.float32)
    audio /= float(np.iinfo(dtype).max)
    audio = audio.reshape(-1, n_ch).T
    _log.debug("Diarization: waveform shape=%s sr=%s", audio.shape, sr)
    audio_in = {"waveform": torch.from_numpy(audio), "sample_rate": sr}

    diarize_kwargs = {}
    if num_speakers:
        diarize_kwargs["num_speakers"] = num_speakers
    _log.debug("Diarization: num_speakers=%s", num_speakers or "auto")

    warnings.filterwarnings("ignore", message=r"std\(\)")
    warnings.filterwarnings("ignore", module="pyannote")

    if on_status:
        on_status("Diarizing...")
    result = pipeline(audio_in, hook=_build_diarize_hook(cancel_token, on_progress), **diarize_kwargs)

    _log.debug("Pipeline result type=%s attrs=%s",
              type(result).__name__, [a for a in dir(result) if not a.startswith("_")])

    annotation = None
    if hasattr(result, "itertracks"):
        annotation = result
    else:
        for attr in dir(result):
            if attr.startswith("_"):
                continue
            try:
                val = getattr(result, attr)
                if hasattr(val, "itertracks"):
                    annotation = val
                    _log.debug("Annotation found in result.%s", attr)
                    break
            except Exception:
                pass

    if annotation is None:
        raise RuntimeError(
            "DiarizeOutput has no itertracks. Attrs: "
            + str([a for a in dir(result) if not a.startswith("_")]))

    turns = [(t.start, t.end, s) for t, _, s in annotation.itertracks(yield_label=True)]
    _log.info("Diarization: done, %d turns", len(turns))
    if on_status:
        on_status("Diarization done.")
    return DiarizeResult(turns=turns, audio=audio, sr=sr)
