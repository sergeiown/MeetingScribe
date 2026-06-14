#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pre-download pyannote + Whisper models to local cache.

Mandatory models (diarization core + minimal recognition) are installed
automatically. Optional models (heavier / higher-accuracy whisper) are offered
for selection.

Usage:
  python download_models.py              interactive: mandatory auto, optional prompted
  python download_models.py --all        mandatory + every optional, no prompts
  python download_models.py --mandatory  mandatory only, no prompts

When stdin is not a terminal (piped) and no flag is given, behaves as --mandatory.
"""

import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

COLORS = {
    "reset":  "\033[0m",
    "cyan":   "\033[96m",
    "green":  "\033[92m",
    "yellow": "\033[93m",
    "red":    "\033[91m",
    "bold":   "\033[1m",
    "dim":    "\033[2m",
}
def c(color, text):
    return f"{COLORS.get(color,'')}{text}{COLORS['reset']}"

SCRIPT_DIR  = Path(__file__).parent
MODELS_DIR  = SCRIPT_DIR / "models"

# ── Model catalog ─────────────────────────────────────────────────────────
# Mandatory pyannote repos. 3.1 + segmentation + wespeaker are the diarization
# pipeline; embedding is used separately for speaker enrollment matching.
# All four are needed for fully offline diarization + known-speaker tagging.
PYANNOTE_MANDATORY = [
    ("pyannote/speaker-diarization-3.1",        "Speaker diarization pipeline (~6 MB cfg)"),
    ("pyannote/segmentation-3.0",               "Segmentation model          (~6 MB)"),
    ("pyannote/wespeaker-voxceleb-resnet34-LM", "Diarization embeddings      (~26 MB)"),
    ("pyannote/embedding",                      "Enrollment embedding        (~17 MB)"),
]

# Mandatory whisper: minimal recognition model. Enough for Ukrainian, smaller+faster.
# Repo id matches faster-whisper's own mapping for "large-v3-turbo", so the model
# fetched here is the same one WhisperModel would load (there is no
# Systran/faster-whisper-large-v3-turbo repo).
WHISPER_MANDATORY = [
    ("large-v3-turbo", "mobiuslabsgmbh/faster-whisper-large-v3-turbo", "large-v3-turbo (~1.6 GB)"),
]

# Optional whisper: heavier, higher accuracy.
WHISPER_OPTIONAL = [
    ("large-v3", "Systran/faster-whisper-large-v3", "large-v3 (~3 GB, higher accuracy, slower)"),
]


def load_config():
    cfg = Path(__file__).parent / "config.env"
    if not cfg.exists():
        return {}
    result = {}
    for line in cfg.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip()
    return result


def _is_complete(model_id):
    """Return True if pyannote model dir has at least one substantial weight/config."""
    slug = "models--" + model_id.replace("/", "--")
    model_dir = MODELS_DIR / slug
    if not model_dir.exists():
        return False
    key_names = {"config.yaml", "config.json", "pytorch_model.bin",
                 "model.safetensors", "pytorch_model.safetensors"}
    for f in model_dir.rglob("*"):
        # 3.1's config.yaml is ~469 B; threshold rejects empty placeholders only.
        if f.is_file() and f.name in key_names and f.stat().st_size > 64:
            return True
    return False


def _is_whisper_complete(model_size):
    """Return True if model.bin exists and is non-empty."""
    model_bin = MODELS_DIR / model_size / "model.bin"
    return model_bin.exists() and model_bin.stat().st_size > 1024 * 1024


def _download_pyannote(model_id, hf_token):
    os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
    from huggingface_hub import snapshot_download
    slug = "models--" + model_id.replace("/", "--")
    local_dir = MODELS_DIR / slug
    local_dir.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=model_id,
        token=hf_token,
        local_dir=str(local_dir),
        ignore_patterns=["*.msgpack", "*.h5", "flax_model*", "tf_model*"],
    )


def _download_whisper(model_size, hf_repo):
    from huggingface_hub import snapshot_download
    local_dir = MODELS_DIR / model_size
    local_dir.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=hf_repo,
        local_dir=str(local_dir),
        ignore_patterns=["*.msgpack", "*.h5", "flax_model*", "tf_model*"],
    )


def _parse_mode(argv):
    args = [a.lower() for a in argv]
    if "--all" in args or "-y" in args:
        return "all"
    if "--mandatory" in args or "--mandatory-only" in args:
        return "mandatory"
    if not sys.stdin.isatty():
        return "mandatory"   # safe default when piped / non-interactive
    return "interactive"


def main():
    mode = _parse_mode(sys.argv[1:])

    os.system("cls" if os.name == "nt" else "clear")
    print(c("cyan", "=" * 60))
    print(c("bold", "  Model downloader"))
    print(c("dim",  f"  Mode: {mode}   Cache: {MODELS_DIR}"))
    print(c("cyan", "=" * 60))
    print()

    MODELS_DIR.mkdir(exist_ok=True)

    cfg = load_config()
    hf_token = os.environ.get("HF_TOKEN") or cfg.get("HF_TOKEN", "")

    # Token is only required for the gated pyannote repos. Whisper repos are public.
    pyannote_needed = [m for m in PYANNOTE_MANDATORY if not _is_complete(m[0])]
    if pyannote_needed and not hf_token:
        if mode == "interactive":
            hf_token = input(c("bold", "  Paste HF_TOKEN (Enter to skip pyannote): ")).strip()
        if not hf_token:
            print(c("yellow", "  No HF_TOKEN - mandatory pyannote models will be skipped."))
            print(c("dim",    "  Set HF_TOKEN in config.env, then re-run."))
            print()

    ok, failed, skipped = [], [], []

    # ── Mandatory: pyannote ────────────────────────────────────────────────
    print(c("bold", "  Mandatory - speaker diarization (installed automatically):"))
    print()
    for model_id, label in PYANNOTE_MANDATORY:
        sys.stdout.write(f"  {label}  ")
        sys.stdout.flush()
        if _is_complete(model_id):
            print(c("green", "already cached"))
            ok.append(model_id)
            continue
        if not hf_token:
            print(c("red", "SKIPPED (no token)"))
            failed.append(model_id)
            continue
        print(c("yellow", "downloading..."))
        try:
            _download_pyannote(model_id, hf_token)
            print(c("green", f"  {label}  done"))
            ok.append(model_id)
        except Exception as ex:
            print(c("red", f"  {label}  FAILED: {ex}"))
            failed.append(model_id)

    # ── Mandatory: whisper ──────────────────────────────────────────────────
    print()
    print(c("bold", "  Mandatory - recognition model (installed automatically):"))
    print()
    for model_size, hf_repo, label in WHISPER_MANDATORY:
        sys.stdout.write(f"  {label}  ")
        sys.stdout.flush()
        if _is_whisper_complete(model_size):
            print(c("green", "already cached"))
            ok.append(model_size)
            continue
        print(c("yellow", "downloading..."))
        try:
            _download_whisper(model_size, hf_repo)
            print(c("green", f"  {label}  done"))
            ok.append(model_size)
        except Exception as ex:
            print(c("red", f"  {label}  FAILED: {ex}"))
            failed.append(model_size)

    # ── Optional: whisper ───────────────────────────────────────────────────
    print()
    print(c("bold", "  Optional - higher-accuracy models:"))
    print()
    for model_size, hf_repo, label in WHISPER_OPTIONAL:
        if _is_whisper_complete(model_size):
            print(f"  {label}  " + c("green", "already cached"))
            ok.append(model_size)
            continue

        if mode == "all":
            want = True
        elif mode == "mandatory":
            want = False
        else:  # interactive
            ans = input(c("bold", f"  Download {label}? [y/N]: ")).strip().lower()
            want = ans in ("y", "yes")

        if not want:
            print(f"  {label}  " + c("dim", "skipped"))
            skipped.append(model_size)
            continue

        sys.stdout.write(f"  {label}  ")
        sys.stdout.flush()
        print(c("yellow", "downloading..."))
        try:
            _download_whisper(model_size, hf_repo)
            print(c("green", f"  {label}  done"))
            ok.append(model_size)
        except Exception as ex:
            print(c("red", f"  {label}  FAILED: {ex}"))
            failed.append(model_size)

    # ── Summary ─────────────────────────────────────────────────────────────
    total = len(PYANNOTE_MANDATORY) + len(WHISPER_MANDATORY) + len(WHISPER_OPTIONAL)
    print()
    print(c("dim", "  " + "-" * 54))
    if ok:
        print(c("green", f"  Ready:   {len(ok)}/{total} models"))
    if skipped:
        print(c("dim",  f"  Skipped: {', '.join(skipped)}  (optional, re-run to add)"))
    if failed:
        print(c("red",   f"  Failed:  {', '.join(failed)}"))
        print(c("yellow", "  Check HF_TOKEN, accepted licenses, and internet connection."))
    print(c("cyan", "=" * 60))
    print()


if __name__ == "__main__":
    main()
