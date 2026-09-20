"""MeetingScribe core: presentation-agnostic transcription/diarization/speaker logic.

Shared by the GUI (and any future frontend). Nothing here prints, reads
stdin, or otherwise assumes a console - progress/status/decisions are all
passed in as optional callbacks by the caller.
"""

import os

# huggingface_hub reads this into a module-level constant the moment it's
# first imported, so it must be set before that import happens anywhere in
# the process - this module is that earliest point. Verified by direct
# testing: with Xet enabled, downloading a large file (whisper "small"'s
# ~460 MB model.bin) repeatedly stalls for minutes at a time (bytes-written
# flat while the process's memory keeps growing) - the exact "app just
# doesn't start" symptom, since the first-run model download would appear
# to hang forever. Disabling Xet falls back to plain HTTP, which completed
# the same download in 98s with zero stalls.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

from .version import VERSION, GITHUB_REPO
from .update_check import check_for_update
from .paths import (
    SCRIPT_DIR, INPUT_DIR, OUTPUT_DIR, MODELS_DIR, LOGS_DIR, SPEAKERS_DIR, SAMPLES_DIR,
    SUPPORTED_EXTENSIONS, ensure_workdirs,
)
from .logging_setup import init_logger, log_ml_versions
from .cancellation import Cancelled, CancelToken, ProgressFn, StatusFn
from .device import (
    detect_device, set_sleep_prevention,
    nvidia_gpu_present, nvidia_gpu_name, detect_hardware_info,
)
from .audio import (
    find_ffmpeg, get_duration, format_duration, format_time, convert_to_wav,
    MediaError, FFPROBE, FFMPEG,
)
from .transcription import transcribe_file, TranscribeResult, Segment
from .diarization import diarize, DiarizeResult
from .speakers import (
    SPEAKER_ID_THRESHOLD,
    base_speaker_name, next_versioned_name, latest_versioned_file, load_speaker_db,
    identify_speaker, assign_speaker, extract_speaker_embeddings,
    collect_speaker_samples, SpeakerNameChoice, NamingDecisionFn,
    resolve_unknown_speakers, enroll_speaker, EnrollResult,
    SpeakerVersion, SpeakerPerson, list_speaker_persons,
    delete_speaker_version, delete_speaker_person, rename_speaker_person,
    export_speakers, import_speakers,
)
from .models_catalog import (
    ModelSpec, WHISPER_MODELS, PYANNOTE_MODELS,
    installed_whisper_sizes, is_whisper_installed, is_pyannote_installed,
    recommend_whisper_model, download_whisper_model, download_pyannote_model,
    whisper_model_bytes,
)
from .config_env import (
    read_config_env, write_config_env, read_hf_token,
    has_diarization_support, is_diar_pipeline_cached,
)
from .results import save_result, save_segments, load_segments, file_status

__all__ = [
    "VERSION", "GITHUB_REPO", "check_for_update",
    "SCRIPT_DIR", "INPUT_DIR", "OUTPUT_DIR", "MODELS_DIR", "LOGS_DIR", "SPEAKERS_DIR", "SAMPLES_DIR",
    "SUPPORTED_EXTENSIONS", "ensure_workdirs",
    "init_logger", "log_ml_versions",
    "Cancelled", "CancelToken", "ProgressFn", "StatusFn",
    "detect_device", "set_sleep_prevention",
    "nvidia_gpu_present", "nvidia_gpu_name", "detect_hardware_info",
    "find_ffmpeg", "get_duration", "format_duration", "format_time", "convert_to_wav",
    "MediaError", "FFPROBE", "FFMPEG",
    "transcribe_file", "TranscribeResult", "Segment",
    "diarize", "DiarizeResult",
    "SPEAKER_ID_THRESHOLD",
    "base_speaker_name", "next_versioned_name", "latest_versioned_file", "load_speaker_db",
    "identify_speaker", "assign_speaker", "extract_speaker_embeddings",
    "collect_speaker_samples", "SpeakerNameChoice", "NamingDecisionFn",
    "resolve_unknown_speakers", "enroll_speaker", "EnrollResult",
    "SpeakerVersion", "SpeakerPerson", "list_speaker_persons",
    "delete_speaker_version", "delete_speaker_person", "rename_speaker_person",
    "export_speakers", "import_speakers",
    "ModelSpec", "WHISPER_MODELS", "PYANNOTE_MODELS",
    "installed_whisper_sizes", "is_whisper_installed", "is_pyannote_installed",
    "recommend_whisper_model", "download_whisper_model", "download_pyannote_model",
    "whisper_model_bytes",
    "read_config_env", "write_config_env", "read_hf_token",
    "has_diarization_support", "is_diar_pipeline_cached",
    "save_result", "save_segments", "load_segments", "file_status",
]
