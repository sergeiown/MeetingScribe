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
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Keep huggingface_hub quiet so our own one-line-per-model status stays readable:
# turn off its tqdm progress bars (they interleave with our lines, and the
# "unauthenticated requests" notice rides on one of them) and lower its logging.
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("HF_HUB_VERBOSITY", "error")   # hide the "unauthenticated requests" notice
import logging
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)

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

# Mandatory whisper: a light, fast, multilingual model. "small" handles Ukrainian
# well enough and is far smaller than the large models (offered as optional below).
WHISPER_MANDATORY = [
    ("small", "Systran/faster-whisper-small", "small (~460 MB, fast, multilingual incl. Ukrainian)"),
]

# Optional whisper: heavier, higher accuracy. Repo ids match faster-whisper's own
# mapping (there is no Systran/faster-whisper-large-v3-turbo repo).
WHISPER_OPTIONAL = [
    ("large-v3-turbo", "mobiuslabsgmbh/faster-whisper-large-v3-turbo", "large-v3-turbo (~1.6 GB, higher accuracy)"),
    ("large-v3",       "Systran/faster-whisper-large-v3",              "large-v3 (~3 GB, best accuracy, slower)"),
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


_IGNORE = ["*.msgpack", "*.h5", "flax_model*", "tf_model*"]


def _download_whisper(model_size, hf_repo, hf_token=None):
    from huggingface_hub import snapshot_download
    local_dir = MODELS_DIR / model_size
    local_dir.mkdir(parents=True, exist_ok=True)
    try:
        snapshot_download(repo_id=hf_repo, token=hf_token or None,
                          local_dir=str(local_dir), ignore_patterns=_IGNORE)
    except Exception as ex:
        # a bad/expired token can 401 even on a public repo -> retry anonymously
        if hf_token and any(s in str(ex) for s in ("401", "403", "authenticated", "credentials")):
            snapshot_download(repo_id=hf_repo, token=None,
                              local_dir=str(local_dir), ignore_patterns=_IGNORE)
        else:
            raise


def _repo_total_bytes(repo_id, hf_token=None):
    """Sum of the (non-ignored) file sizes in a HF repo, for the progress bar. 0 if unknown."""
    try:
        import fnmatch
        from huggingface_hub import HfApi
        info = HfApi().model_info(repo_id, files_metadata=True, token=hf_token or None)
        total = 0
        for s in info.siblings:
            name = s.rfilename or ""
            if any(fnmatch.fnmatch(name, p) for p in _IGNORE):
                continue
            total += (s.size or 0)
        return total
    except Exception:
        return 0


def _dir_size(path):
    total = 0
    for f in Path(path).rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return total


def _fmt_size(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def _fmt_eta(sec):
    sec = int(sec)
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def _render_progress(label, cur, total, t0):
    width = 24
    if total > 0:
        pct = min(cur / total, 1.0)
        filled = int(width * pct)
        bar = "█" * filled + "░" * (width - filled)
        eta = ""
        if pct > 0.02:
            eta = "  ETA " + _fmt_eta((time.time() - t0) / pct * (1 - pct))
        line = (f"  {label}  [{bar}] {int(pct*100):3d}%  "
                f"{_fmt_size(cur)}/{_fmt_size(total)}{eta}")
    else:
        line = f"  {label}  downloading... {_fmt_size(cur)}"
    sys.stdout.write("\r\033[2K" + line)
    sys.stdout.flush()


def _download_whisper_step(model_size, hf_repo, label, hf_token):
    """Download a whisper model showing a single in-place progress bar with ETA.
    Returns (ok, exception)."""
    import threading
    local_dir = MODELS_DIR / model_size
    total = _repo_total_bytes(hf_repo, hf_token)
    err = {}

    def worker():
        try:
            _download_whisper(model_size, hf_repo, hf_token)
        except Exception as ex:
            err["ex"] = ex

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t0 = time.time()
    while t.is_alive():
        _render_progress(label, _dir_size(local_dir), total, t0)
        time.sleep(0.4)
    t.join()
    if "ex" in err:
        sys.stdout.write("\r\033[2K")
        sys.stdout.flush()
        return False, err["ex"]
    sys.stdout.write("\r\033[2K  " + label + "  " + c("green", "done") + "\n")
    sys.stdout.flush()
    return True, None


def _parse_mode(argv):
    args = [a.lower() for a in argv]
    if "--all" in args or "-y" in args:
        return "all"
    if "--mandatory" in args or "--mandatory-only" in args:
        return "mandatory"
    if not sys.stdin.isatty():
        return "mandatory"   # safe default when piped / non-interactive
    return "interactive"


def _download_step(label, fn):
    """Show one in-place status line: "label  downloading..." while fn() runs,
    then overwrite it with the final status. Returns (ok, exception)."""
    sys.stdout.write(f"  {label}  " + c("yellow", "downloading..."))
    sys.stdout.flush()
    try:
        fn()
    except Exception as ex:
        sys.stdout.write("\r\033[2K")          # clear the line; caller prints the reason
        sys.stdout.flush()
        return False, ex
    sys.stdout.write("\r\033[2K  " + label + "  " + c("green", "done") + "\n")
    sys.stdout.flush()
    return True, None


def _set_sleep_prevention(active):
    """Windows: keep the machine awake during long downloads. No-op elsewhere."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ES_CONTINUOUS      = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        if active:
            ctypes.windll.kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        else:
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
    except Exception:
        pass


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
    hf_token = (os.environ.get("HF_TOKEN") or cfg.get("HF_TOKEN", "")).strip()
    if hf_token in ("", "hf_YOUR_TOKEN_HERE"):   # the example placeholder is not a real token
        hf_token = ""

    # Keep the machine awake while we download (can take many minutes).
    _set_sleep_prevention(True)
    import atexit
    atexit.register(_set_sleep_prevention, False)

    # Token is only required for the gated pyannote repos. Whisper repos are public.
    pyannote_needed = [m for m in PYANNOTE_MANDATORY if not _is_complete(m[0])]
    if pyannote_needed and not hf_token:
        if mode == "interactive":
            hf_token = input(c("bold", "  Paste HF_TOKEN (Enter to skip diarization models): ")).strip()
        if not hf_token:
            print(c("yellow", "  No valid HF_TOKEN set:"))
            print(c("dim",    "    - speaker-diarization models will be skipped (transcription still works)"))
            print(c("dim",    "    - downloads are unauthenticated, so they may be noticeably slower"))
            print(c("dim",    "  To enable diarization and faster downloads, put a real token in"))
            print(c("dim",    "  config.env and accept the model licenses (see README), then re-run."))
            print()
            if mode == "interactive":
                ans = input(c("bold", "  Continue without a token? [Y/n]: ")).strip().lower()
                if ans in ("n", "no", "q", "exit"):
                    print(c("dim", "  Cancelled."))
                    sys.exit(0)
                print()

    ok, failed, skipped, needs_token = [], [], [], []

    # ── Mandatory: pyannote ────────────────────────────────────────────────
    print(c("bold", "  Mandatory - speaker diarization (installed automatically):"))
    print()
    for model_id, label in PYANNOTE_MANDATORY:
        if _is_complete(model_id):
            print("  " + label + "  " + c("green", "already cached"))
            ok.append(model_id)
            continue
        if not hf_token:
            print("  " + label + "  " + c("yellow", "skipped (needs token)"))
            needs_token.append(model_id)
            continue
        done, ex = _download_step(label, lambda mid=model_id: _download_pyannote(mid, hf_token))
        if done:
            ok.append(model_id)
        elif any(s in str(ex) for s in ("401", "403", "gated", "restricted", "authenticated", "Cannot access")):
            print("  " + label + "  " + c("red", "access denied (token invalid or license not accepted)"))
            needs_token.append(model_id)
        else:
            print("  " + label + "  " + c("red", f"failed: {str(ex).splitlines()[0][:80]}"))
            failed.append(model_id)

    # ── Mandatory: whisper ──────────────────────────────────────────────────
    print()
    print(c("bold", "  Mandatory - recognition model (installed automatically):"))
    print()
    for model_size, hf_repo, label in WHISPER_MANDATORY:
        if _is_whisper_complete(model_size):
            print("  " + label + "  " + c("green", "already cached"))
            ok.append(model_size)
            continue
        done, ex = _download_whisper_step(model_size, hf_repo, label, hf_token)
        if done:
            ok.append(model_size)
        else:
            print("  " + label + "  " + c("red", f"failed: {str(ex).splitlines()[0][:80]}"))
            failed.append(model_size)

    # ── Optional: whisper ───────────────────────────────────────────────────
    print()
    print(c("bold", "  Optional - higher-accuracy models:"))
    print()
    for model_size, hf_repo, label in WHISPER_OPTIONAL:
        if _is_whisper_complete(model_size):
            print("  " + label + "  " + c("green", "already cached"))
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
            print("  " + label + "  " + c("dim", "skipped"))
            skipped.append(model_size)
            continue

        done, ex = _download_whisper_step(model_size, hf_repo, label, hf_token)
        if done:
            ok.append(model_size)
        else:
            print("  " + label + "  " + c("red", f"failed: {str(ex).splitlines()[0][:80]}"))
            failed.append(model_size)

    # ── Summary ─────────────────────────────────────────────────────────────
    total = len(PYANNOTE_MANDATORY) + len(WHISPER_MANDATORY) + len(WHISPER_OPTIONAL)
    print()
    print(c("dim", "  " + "-" * 54))
    if ok:
        print(c("green", f"  Ready:   {len(ok)}/{total} models"))
    if skipped:
        print(c("dim",  f"  Skipped: {', '.join(skipped)}  (optional, re-run to add)"))
    if needs_token:
        print(c("yellow", "  Diarization models skipped - they need an HF_TOKEN:"))
        print(c("dim",    "    1. put a real token in config.env  (HF_TOKEN=hf_...)"))
        print(c("dim",    "    2. accept the model licenses on huggingface.co (see README)"))
        print(c("dim",    "    3. re-run this. Transcription already works without them."))
    if failed:
        print(c("red",   f"  Failed:  {', '.join(failed)}"))
        print(c("yellow", "  Check your token, accepted licenses, and internet connection."))
    print(c("cyan", "=" * 60))
    print()


if __name__ == "__main__":
    main()
