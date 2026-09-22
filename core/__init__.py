"""MeetingScribe core: presentation-agnostic transcription/diarization/speaker logic.

Nothing here prints or reads stdin - progress/status/decisions are passed
in as optional callbacks by the caller.
"""

import os

# huggingface_hub reads this into a module-level constant on first import,
# so it must be set here, before huggingface_hub is imported anywhere else.
# Xet transfer can stall indefinitely on large downloads; HTTP fallback doesn't.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

from .version import VERSION, GITHUB_REPO
from .update_check import (
    check_for_update, download_installer, cleanup_update_downloads,
    spawn_installer, UPDATE_DOWNLOAD_DIR,
)
from .paths import (
    SCRIPT_DIR, INPUT_DIR, OUTPUT_DIR, MODELS_DIR, LOGS_DIR, SPEAKERS_DIR, SAMPLES_DIR, SEGMENTS_DIR,
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
    DIARIZATION_BUNDLE_LABEL, DIARIZATION_BUNDLE_SIZE, DIARIZATION_BUNDLE_DESCRIPTION,
    installed_whisper_sizes, is_whisper_installed, is_pyannote_installed, is_diarization_complete,
    recommend_whisper_model, download_whisper_model, download_pyannote_model,
    download_diarization_models, whisper_model_bytes,
    delete_whisper_model, delete_diarization_models,
)
from .config_env import (
    read_config_env, write_config_env, read_hf_token,
    has_diarization_support, is_diar_pipeline_cached,
)
from .results import save_result, save_segments, load_segments, file_status
from .recording import (
    RecordingError, AudioDevice, Recorder, list_microphones, list_loopback_outputs,
)

__all__ = [
    "VERSION", "GITHUB_REPO", "check_for_update",
    "download_installer", "cleanup_update_downloads", "spawn_installer", "UPDATE_DOWNLOAD_DIR",
    "SCRIPT_DIR", "INPUT_DIR", "OUTPUT_DIR", "MODELS_DIR", "LOGS_DIR", "SPEAKERS_DIR", "SAMPLES_DIR", "SEGMENTS_DIR",
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
    "DIARIZATION_BUNDLE_LABEL", "DIARIZATION_BUNDLE_SIZE", "DIARIZATION_BUNDLE_DESCRIPTION",
    "installed_whisper_sizes", "is_whisper_installed", "is_pyannote_installed", "is_diarization_complete",
    "recommend_whisper_model", "download_whisper_model", "download_pyannote_model",
    "download_diarization_models", "whisper_model_bytes",
    "delete_whisper_model", "delete_diarization_models",
    "read_config_env", "write_config_env", "read_hf_token",
    "has_diarization_support", "is_diar_pipeline_cached",
    "save_result", "save_segments", "load_segments", "file_status",
    "RecordingError", "AudioDevice", "Recorder", "list_microphones", "list_loopback_outputs",
]
