"""Recognition (Whisper) and diarization (pyannote) model catalogs, install
status, and download."""

import fnmatch
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .cancellation import CancelToken, ProgressFn
from .paths import MODELS_DIR

_IGNORE_PATTERNS = ["*.msgpack", "*.h5", "flax_model*", "tf_model*"]


@dataclass(frozen=True)
class ModelSpec:
    key: str
    hf_repo: str
    label: str  # no size suffix - would clip in the sidebar's narrow combo
    size: str
    description: str
    mandatory: bool


# Heaviest-first: recommend_whisper_model() relies on this order.
WHISPER_MODELS = [
    ModelSpec("large-v3", "Systran/faster-whisper-large-v3",
              "large-v3", "~3 GB",
              "Best accuracy, slowest - for when quality matters more than speed.", False),
    ModelSpec("large-v3-turbo", "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
              "large-v3-turbo", "~1.6 GB",
              "Nearly large-v3's accuracy at a fraction of the time - a good default.", False),
    ModelSpec("small", "Systran/faster-whisper-small",
              "small", "~460 MB",
              "Fastest and lightest - good for quick drafts or low-power machines.", True),
]

PYANNOTE_MODELS = [
    ModelSpec("pyannote/speaker-diarization-3.1", "pyannote/speaker-diarization-3.1",
              "Speaker diarization pipeline", "~6 MB",
              "Splits the audio into speaker turns - who spoke when.", True),
    ModelSpec("pyannote/segmentation-3.0", "pyannote/segmentation-3.0",
              "Segmentation model", "~6 MB",
              "Detects speech boundaries within the audio for the pipeline above.", True),
    ModelSpec("pyannote/wespeaker-voxceleb-resnet34-LM", "pyannote/wespeaker-voxceleb-resnet34-LM",
              "Diarization embeddings", "~26 MB",
              "Turns each speaker turn into a voice fingerprint used during diarization.", True),
    ModelSpec("pyannote/embedding", "pyannote/embedding",
              "Enrollment embedding", "~17 MB",
              "Used to enroll and recognize your saved, known speakers.", True),
]

# Offered as one combined download - keep DIARIZATION_BUNDLE_SIZE in sync
# with the sum of the individual sizes above.
DIARIZATION_BUNDLE_LABEL = "Diarization models"
DIARIZATION_BUNDLE_SIZE = "~55 MB"
DIARIZATION_BUNDLE_DESCRIPTION = (
    "Everything needed for speaker diarization: the pipeline, "
    "segmentation, voice fingerprinting, and speaker enrollment.")


def is_whisper_installed(size: str, models_dir: Path = MODELS_DIR) -> bool:
    model_bin = models_dir / size / "model.bin"
    return model_bin.exists() and model_bin.stat().st_size > 1024 * 1024


def is_pyannote_installed(model_id: str, models_dir: Path = MODELS_DIR) -> bool:
    slug = "models--" + model_id.replace("/", "--")
    model_dir = models_dir / slug
    if not model_dir.exists():
        return False
    key_names = {"config.yaml", "config.json", "pytorch_model.bin",
                 "model.safetensors", "pytorch_model.safetensors"}
    for f in model_dir.rglob("*"):
        # 3.1's config.yaml is ~469 B; threshold rejects empty placeholders only.
        if f.is_file() and f.name in key_names and f.stat().st_size > 64:
            return True
    return False


def is_diarization_complete(models_dir: Path = MODELS_DIR) -> bool:
    return all(is_pyannote_installed(spec.key, models_dir) for spec in PYANNOTE_MODELS)


def installed_whisper_sizes(models_dir: Path = MODELS_DIR) -> list:
    return [spec.key for spec in WHISPER_MODELS if is_whisper_installed(spec.key, models_dir)]


def recommend_whisper_model(installed: list, device: str) -> Optional[str]:
    """Heaviest-first catalog order; recommend the heaviest installed model
    on 'cuda', the lightest installed one on 'cpu'. None if nothing installed."""
    ordered = [spec.key for spec in WHISPER_MODELS if spec.key in installed]
    if not ordered:
        return None
    return ordered[0] if device == "cuda" else ordered[-1]


def _dir_size(path) -> int:
    total = 0
    for f in Path(path).rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return total


def _repo_total_bytes(repo_id: str, hf_token: Optional[str] = None) -> int:
    """Sum of the (non-ignored) file sizes in a HF repo, for the progress
    denominator. 0 if unknown (degrades to an indeterminate progress read)."""
    try:
        from huggingface_hub import HfApi
        info = HfApi().model_info(repo_id, files_metadata=True, token=hf_token or None)
        total = 0
        for s in info.siblings:
            name = s.rfilename or ""
            if any(fnmatch.fnmatch(name, p) for p in _IGNORE_PATTERNS):
                continue
            total += (s.size or 0)
        return total
    except Exception:
        return 0


def whisper_model_bytes(spec: ModelSpec, hf_token: Optional[str] = None) -> int:
    """Real total download size for spec, for a caller that wants to fold it
    into a larger combined progress total (e.g. the first-run bootstrap)."""
    return _repo_total_bytes(spec.hf_repo, hf_token)


def _run_with_progress(download_fn, local_dir: Path, total: int,
                        cancel_token: Optional[CancelToken],
                        on_progress: Optional[ProgressFn]) -> None:
    """Runs download_fn() on a background thread, polling local_dir's size
    for progress. snapshot_download() has no cancel hook - cancel_token
    only stops our polling; the transfer itself keeps running to completion."""
    err = {}

    def worker():
        try:
            download_fn()
        except Exception as ex:
            err["ex"] = ex

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    while t.is_alive():
        if on_progress:
            on_progress(float(_dir_size(local_dir)), float(total), "downloading")
        if cancel_token:
            cancel_token.check()
        time.sleep(0.4)
    t.join()
    if "ex" in err:
        raise err["ex"]
    if on_progress:
        on_progress(float(total or 1), float(total or 1), "done")


def _snapshot_download_whisper(size: str, hf_repo: str, hf_token: Optional[str],
                                models_dir: Path) -> None:
    from huggingface_hub import snapshot_download
    local_dir = models_dir / size
    local_dir.mkdir(parents=True, exist_ok=True)
    try:
        snapshot_download(repo_id=hf_repo, token=hf_token or None,
                          local_dir=str(local_dir), ignore_patterns=_IGNORE_PATTERNS)
    except Exception as ex:
        # a bad/expired token can 401 even on a public repo -> retry anonymously
        if hf_token and any(s in str(ex) for s in ("401", "403", "authenticated", "credentials")):
            snapshot_download(repo_id=hf_repo, token=None,
                              local_dir=str(local_dir), ignore_patterns=_IGNORE_PATTERNS)
        else:
            raise


def download_whisper_model(size: str, hf_repo: str, hf_token: Optional[str] = None, *,
                            models_dir: Path = MODELS_DIR,
                            cancel_token: Optional[CancelToken] = None,
                            on_progress: Optional[ProgressFn] = None) -> None:
    local_dir = models_dir / size
    total = _repo_total_bytes(hf_repo, hf_token)
    _run_with_progress(
        lambda: _snapshot_download_whisper(size, hf_repo, hf_token, models_dir),
        local_dir, total, cancel_token, on_progress)


def download_pyannote_model(model_id: str, hf_token: str, *,
                             models_dir: Path = MODELS_DIR,
                             cancel_token: Optional[CancelToken] = None,
                             on_progress: Optional[ProgressFn] = None) -> None:
    from huggingface_hub import snapshot_download
    os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
    slug = "models--" + model_id.replace("/", "--")
    local_dir = models_dir / slug
    local_dir.mkdir(parents=True, exist_ok=True)
    total = _repo_total_bytes(model_id, hf_token)

    def do_download():
        snapshot_download(repo_id=model_id, token=hf_token,
                          local_dir=str(local_dir), ignore_patterns=_IGNORE_PATTERNS)

    _run_with_progress(do_download, local_dir, total, cancel_token, on_progress)


def download_diarization_models(hf_token: str, *, models_dir: Path = MODELS_DIR,
                                  cancel_token: Optional[CancelToken] = None,
                                  on_progress: Optional[ProgressFn] = None) -> None:
    """Downloads every PYANNOTE_MODELS entry as one combined operation with
    a single overall progress fraction."""
    to_fetch = [s for s in PYANNOTE_MODELS if not is_pyannote_installed(s.key, models_dir)]
    if not to_fetch:
        return
    sizes = [_repo_total_bytes(s.hf_repo, hf_token) for s in to_fetch]
    total = sum(sizes) or 1
    bytes_before = 0
    for spec, size in zip(to_fetch, sizes):
        if cancel_token:
            cancel_token.check()

        def relay(current, _sub_total, label, bytes_before=bytes_before):
            if on_progress:
                on_progress(bytes_before + current, total, label)

        download_pyannote_model(spec.key, hf_token, models_dir=models_dir,
                                cancel_token=cancel_token, on_progress=relay)
        bytes_before += size
    if on_progress:
        on_progress(float(total), float(total), "done")
