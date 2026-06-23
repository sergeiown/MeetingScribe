#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Interactive transcription launcher. Run via run.bat"""

import os
import re
import sys
import subprocess
import tempfile
import time
import logging
import shutil
import contextlib
import io as _io
from datetime import datetime
from pathlib import Path

@contextlib.contextmanager
def _silence():
    """Suppress stdout/stderr at OS fd level + Python stream level + warnings."""
    import warnings as _w
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    saved_out  = os.dup(1)
    saved_err  = os.dup(2)
    os.dup2(devnull_fd, 1)
    os.dup2(devnull_fd, 2)
    os.close(devnull_fd)
    dn_out = open(os.devnull, "w")
    dn_err = open(os.devnull, "w")
    old_stdout, sys.stdout = sys.stdout, dn_out
    old_stderr, sys.stderr = sys.stderr, dn_err
    try:
        with _w.catch_warnings():
            _w.simplefilter("ignore")
            yield
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        dn_out.close()
        dn_err.close()
        os.dup2(saved_out, 1)
        os.dup2(saved_err, 2)
        os.close(saved_out)
        os.close(saved_err)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# torch on Windows has no triton; silence its W-level log spam (flop_counter etc.)
os.environ.setdefault("TORCH_CPP_LOG_LEVEL", "ERROR")
logging.getLogger("torch").setLevel(logging.ERROR)

print("[*] Initializing transcription engine...", flush=True)

SCRIPT_DIR = Path(__file__).parent
INPUT_DIR  = SCRIPT_DIR / "input"
OUTPUT_DIR = SCRIPT_DIR / "output"
MODELS_DIR = SCRIPT_DIR / "models"
LOGS_DIR     = SCRIPT_DIR / "logs"
SPEAKERS_DIR = SCRIPT_DIR / "speakers"

SPEAKER_ID_THRESHOLD = 0.75   # cosine similarity cutoff for name match

def _get_cpu_name():
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        name = winreg.QueryValueEx(key, "ProcessorNameString")[0]
        winreg.CloseKey(key)
        return name.strip()
    except Exception:
        pass
    try:
        import platform
        return platform.processor() or "CPU"
    except Exception:
        return "CPU"

def _add_cuda_dll_dirs():
    """Windows: ctranslate2 needs cuBLAS/cuDNN DLLs; torch ships them in torch\\lib.
    Also covers nvidia-* pip packages (site-packages/nvidia/*/bin)."""
    if os.name != "nt" or getattr(_add_cuda_dll_dirs, "_done", False):
        return
    _add_cuda_dll_dirs._done = True
    try:
        import torch
        candidates = [Path(torch.__file__).parent / "lib"]
        import site
        for sp in site.getsitepackages():
            nv = Path(sp) / "nvidia"
            if nv.is_dir():
                candidates += list(nv.glob("*/bin"))
        for d in candidates:
            if d.is_dir():
                os.add_dll_directory(str(d))
                os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        pass

def _detect_device():
    """Returns (device_str, compute_type, device_name)."""
    try:
        import torch
        if torch.cuda.is_available():
            _add_cuda_dll_dirs()
            major, _ = torch.cuda.get_device_capability(0)
            # Pascal (sm_6x) and older have no efficient fp16 -> int8_float32
            ctype = "float16" if major >= 7 else "int8_float32"
            return "cuda", ctype, torch.cuda.get_device_name(0)
    except Exception:
        pass
    return "cpu", "int8", _get_cpu_name()

def _set_sleep_prevention(active: bool):
    if os.name != "nt":
        return
    try:
        import ctypes
        ES_CONTINUOUS       = 0x80000000
        ES_SYSTEM_REQUIRED  = 0x00000001
        ES_DISPLAY_REQUIRED = 0x00000002
        if active:
            ctypes.windll.kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
        else:
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
    except Exception:
        pass

def _init_logger():
    LOGS_DIR.mkdir(exist_ok=True)
    log_path = LOGS_DIR / "session.log"
    logger = logging.getLogger("audio_processor")
    logger.setLevel(logging.DEBUG)
    fh = logging.FileHandler(log_path, encoding="utf-8", mode="w")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(fh)
    logger.info("=== Session started %s ===", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    try:
        import faster_whisper
        logger.debug("faster-whisper %s", faster_whisper.__version__)
    except Exception:
        pass
    try:
        import torch
        logger.debug("torch %s", torch.__version__)
        # must run AFTER torch import: torch._logging resets "torch" logger to WARNING
        logging.getLogger("torch").setLevel(logging.ERROR)
    except Exception:
        pass
    try:
        with _silence():
            import pyannote.audio
        logger.debug("pyannote.audio %s", pyannote.audio.__version__)
    except Exception:
        pass
    return logger, log_path

SESSION_LOG, SESSION_LOG_PATH = _init_logger()

SUPPORTED = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4a", ".mp3", ".wav"}

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

def header():
    os.system("cls" if os.name == "nt" else "clear")
    print(c("cyan", "=" * 60))
    print(c("bold", "  MeetingScribe - Whisper Transcription"))
    print(c("dim",  "  Local processing via faster-whisper"))
    print(c("cyan", "=" * 60))
    print()

def check_deps():
    missing = []
    try:
        import faster_whisper
    except ImportError:
        missing.append("faster-whisper")
    if missing:
        print(c("yellow", "  Installing: " + ", ".join(missing) + "..."))
        subprocess.run([sys.executable, "-m", "pip", "install"] + missing + ["-q"],
                       check=True)
        print(c("green", "  Done.\n"))

def has_diarization():
    try:
        import torch
        with _silence():
            import pyannote.audio
        return True
    except ImportError:
        return False

def diar_models_cached():
    """True if the pyannote diarization pipeline is present locally (offline-ready)."""
    base = MODELS_DIR / "models--pyannote--speaker-diarization-3.1"
    if not base.exists():
        return False
    for f in base.rglob("config.yaml"):
        if f.is_file() and f.stat().st_size > 64:
            return True
    return False

# --- Progress bars ---
_PROG = [0]

def progress_bar(current, total, width=40, label="", start_time=None):
    if total <= 0:
        return
    pct    = min(current / total, 1.0)
    filled = int(width * pct)
    bar    = "█" * filled + "░" * (width - filled)
    el     = format_time(current)
    tot    = format_time(total)
    eta_str = ""
    if start_time is not None and pct > 0.02:
        elapsed = time.time() - start_time
        eta_sec = elapsed / pct * (1.0 - pct)
        eta_str = f"  ETA {format_time(eta_sec)}"
    if _PROG[0]:
        sys.stdout.write(f"\033[{_PROG[0]}A")
    sys.stdout.write(f"\033[2K  [{bar}] {int(pct*100):3d}%  {el}/{tot}{eta_str}\n")
    if label:
        sys.stdout.write(f"\033[2K  \033[2m[{label[:58]}]\033[0m\n")
        _PROG[0] = 2
    else:
        _PROG[0] = 1
    sys.stdout.flush()

_DIARZ_LINE = [False]
_DIARZ_T0   = [None]

def _diarize_hook(step_name, step_artifact, file=None, total=None, completed=None):
    if total is None or completed is None or total == 0:
        return
    if _DIARZ_T0[0] is None:
        _DIARZ_T0[0] = time.time()
    pct    = min(int(completed / total * 100), 100)
    filled = pct * 38 // 100
    bar    = "█" * filled + "░" * (38 - filled)
    eta_str = ""
    if pct > 1:
        elapsed = time.time() - _DIARZ_T0[0]
        eta_sec = elapsed / (pct / 100.0) * (1.0 - pct / 100.0)
        eta_str = f"  ETA {format_time(eta_sec)}"
    if _DIARZ_LINE[0]:
        sys.stdout.write("\033[2A")
    sys.stdout.write(f"\033[2K  [{bar}] {pct:3d}%{eta_str}\n")
    sys.stdout.write(f"\033[2K  \033[2m[{step_name}]\033[0m\n")
    sys.stdout.flush()
    _DIARZ_LINE[0] = True

def diarize(wav_path, hf_token, num_speakers=None):
    import torch
    import wave as _wave
    import numpy as _np
    with _silence():
        from pyannote.audio import Pipeline

    tok_hint = f"{hf_token[:8]}...{hf_token[-4:]}" if len(hf_token) > 12 else "???"
    SESSION_LOG.debug("Diarization: token=%s  wav=%s", tok_hint, wav_path)
    SESSION_LOG.info("Diarization: loading pipeline pyannote/speaker-diarization-3.1")

    print(c("bold", "  Diarization:"))
    print()
    _DIARZ_LINE[0] = False
    _DIARZ_T0[0]   = None

    MODELS_DIR.mkdir(exist_ok=True)
    _diar_local = MODELS_DIR / "models--pyannote--speaker-diarization-3.1"
    with _silence():
        try:
            if _diar_local.exists():
                pipeline = Pipeline.from_pretrained(str(_diar_local), token=hf_token)
            else:
                try:
                    pipeline = Pipeline.from_pretrained(
                        "pyannote/speaker-diarization-3.1",
                        token=hf_token,
                        cache_dir=str(MODELS_DIR),
                        local_files_only=True)
                except Exception:
                    pipeline = Pipeline.from_pretrained(
                        "pyannote/speaker-diarization-3.1",
                        token=hf_token,
                        cache_dir=str(MODELS_DIR))
        except Exception:
            pipeline = Pipeline.from_pretrained(
                "pyannote/speaker-diarization-3.1",
                token=hf_token)

    _dev, _, _gpu = _detect_device()
    with _silence():
        pipeline = pipeline.to(torch.device(_dev))
    SESSION_LOG.info("Diarization: device=%s", _dev)

    with _wave.open(wav_path, "rb") as wf:
        sr    = wf.getframerate()
        n_ch  = wf.getnchannels()
        sw    = wf.getsampwidth()
        raw   = wf.readframes(wf.getnframes())
    dtype  = {1: _np.int8, 2: _np.int16, 4: _np.int32}.get(sw, _np.int16)
    audio  = _np.frombuffer(raw, dtype=dtype).astype(_np.float32)
    audio /= float(_np.iinfo(dtype).max)
    audio  = audio.reshape(-1, n_ch).T
    SESSION_LOG.debug("Diarization: waveform shape=%s  sr=%s", audio.shape, sr)
    audio_in = {"waveform": torch.from_numpy(audio), "sample_rate": sr}

    diarize_kwargs = {}
    if num_speakers:
        diarize_kwargs["num_speakers"] = num_speakers
        SESSION_LOG.debug("Diarization: num_speakers=%d", num_speakers)
    else:
        SESSION_LOG.debug("Diarization: num_speakers=auto")

    import warnings as _w
    _w.filterwarnings("ignore", message="std\\(\\)")
    _w.filterwarnings("ignore", module="pyannote")
    result = pipeline(audio_in, hook=_diarize_hook, **diarize_kwargs)
    _DIARZ_LINE[0] = False
    print()

    SESSION_LOG.debug("Pipeline result type=%s  attrs=%s",
                      type(result).__name__,
                      [a for a in dir(result) if not a.startswith("_")])

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
                    SESSION_LOG.debug("Annotation found in result.%s", attr)
                    break
            except Exception:
                pass

    if annotation is None:
        raise RuntimeError(
            "DiarizeOutput has no itertracks. Attrs: "
            + str([a for a in dir(result) if not a.startswith("_")])
        )

    turns = [(t.start, t.end, s) for t, _, s in annotation.itertracks(yield_label=True)]
    print(c("green", "  Done."))
    SESSION_LOG.info("Diarization: done, %d turns", len(turns))
    return turns, audio, sr

_SPK_VER_RE = re.compile(r"\s*\(v(\d+)\)$")

def base_speaker_name(name):
    """'Віталій Рубан (v2)' -> 'Віталій Рубан'."""
    return _SPK_VER_RE.sub("", name).strip()

def next_versioned_name(name):
    """Next free version for this person in speakers/: first time -> (v1),
    otherwise (vN+1). Unversioned existing file counts as v1."""
    base  = base_speaker_name(name)
    max_v = 0
    if SPEAKERS_DIR.exists():
        for f in SPEAKERS_DIR.glob("*.npy"):
            if base_speaker_name(f.stem) == base:
                m = _SPK_VER_RE.search(f.stem)
                max_v = max(max_v, int(m.group(1)) if m else 1)
    return f"{base} (v{max_v + 1})"

def latest_versioned_file(name):
    """Return Path of the highest-version .npy for this person, or None."""
    base = base_speaker_name(name)
    best, best_v = None, -1
    if SPEAKERS_DIR.exists():
        for f in SPEAKERS_DIR.glob("*.npy"):
            if base_speaker_name(f.stem) == base:
                m = _SPK_VER_RE.search(f.stem)
                v = int(m.group(1)) if m else 1
                if v > best_v:
                    best_v, best = v, f
    return best

def load_speaker_db():
    """Load all .npy embeddings from speakers/. Returns {name: np.array}."""
    if not SPEAKERS_DIR.exists():
        return {}
    import numpy as _np
    db = {}
    for f in SPEAKERS_DIR.glob("*.npy"):
        try:
            db[f.stem] = _np.load(str(f))
        except Exception:
            pass
    return db

def _cosine_sim(a, b):
    import numpy as _np
    a = a / (_np.linalg.norm(a) + 1e-10)
    b = b / (_np.linalg.norm(b) + 1e-10)
    return float(_np.dot(a, b))

def identify_speaker(emb, db):
    """Best cosine match in db. Returns (name, score) - name is None if below threshold."""
    best_name, best_score = None, -1.0
    for name, ref in db.items():
        score = _cosine_sim(emb, ref)
        if score > best_score:
            best_score = score
            best_name  = name
    if best_score >= SPEAKER_ID_THRESHOLD:
        return best_name, best_score
    return None, best_score

def _find_model_dir(base_dir):
    """Find directory with model weights (root or snapshots/<commit>/)."""
    weight_names = {"pytorch_model.bin", "model.safetensors",
                    "pytorch_model.safetensors"}
    for name in weight_names:
        if (base_dir / name).exists():
            return base_dir
    snaps = base_dir / "snapshots"
    if snaps.exists():
        for commit_dir in sorted(snaps.iterdir(), reverse=True):
            if commit_dir.is_dir():
                for name in weight_names:
                    if (commit_dir / name).exists():
                        return commit_dir
    return None

def _load_embedding_infer(hf_token):
    """Load pyannote/embedding model from local cache. Returns Inference object."""
    import torch as _torch
    with _silence():
        from pyannote.audio import Inference, Model
    _dev, _, _ = _detect_device()
    _torch_dev  = _torch.device(_dev)
    _emb_base = MODELS_DIR / "models--pyannote--embedding"
    _emb_dir  = _find_model_dir(_emb_base) if _emb_base.exists() else None
    if _emb_dir is not None:
        _prev = os.environ.get("HF_HUB_OFFLINE")
        os.environ["HF_HUB_OFFLINE"] = "1"
        try:
            with _silence():
                emb_model = Model.from_pretrained(str(_emb_dir), token=hf_token)
            with _silence():
                emb_model = emb_model.to(_torch_dev)
            return Inference(emb_model, window="whole")
        except TypeError:
            with _silence():
                emb_model = Model.from_pretrained(str(_emb_dir), use_auth_token=hf_token)
            with _silence():
                emb_model = emb_model.to(_torch_dev)
            return Inference(emb_model, window="whole")
        finally:
            if _prev is None:
                os.environ.pop("HF_HUB_OFFLINE", None)
            else:
                os.environ["HF_HUB_OFFLINE"] = _prev
    # fallback: try network
    print(c("yellow", "  Embedding model not cached locally - attempting download..."))
    try:
        emb_model = Model.from_pretrained("pyannote/embedding", token=hf_token)
    except TypeError:
        emb_model = Model.from_pretrained("pyannote/embedding", use_auth_token=hf_token)
    return Inference(emb_model, window="whole")

def extract_speaker_embeddings(audio_np, sr, turns, hf_token, db):
    """
    For each unique SPEAKER_XX label in turns, extract embedding and match against db.
    Returns dict {speaker_label: name}. Missing means below threshold.
    """
    import torch
    import numpy as _np
    unique = list(set(s for _, _, s in turns))
    if not unique or not db:
        return {}

    print(c("bold", "  Speaker identification..."))
    infer = _load_embedding_infer(hf_token)

    result = {}
    for lbl in sorted(unique):
        segs = [(s, e) for s, e, sp in turns if sp == lbl]
        chunks, total_s = [], 0.0
        for s, e in sorted(segs, key=lambda x: x[1]-x[0], reverse=True):
            if total_s >= 60 or e - s < 0.5:
                continue
            si = int(s * sr)
            ei = min(int(e * sr), audio_np.shape[1])
            chunk = audio_np[:, si:ei]
            if chunk.shape[1] > 0:
                chunks.append(chunk)
                total_s += (e - s)
        if not chunks:
            continue
        try:
            combined  = _np.concatenate(chunks, axis=1)
            audio_in  = {"waveform": torch.from_numpy(combined), "sample_rate": sr}
            emb       = _np.array(infer(audio_in)).flatten()
            name, score = identify_speaker(emb, db)
            if name:
                SESSION_LOG.info("Speaker ID: %s -> %s (%.2f)", lbl, name, score)
                print(c("dim", f"    {lbl} -> {name} ({score:.2f})"))
                result[lbl] = base_speaker_name(name)
            else:
                SESSION_LOG.info("Speaker ID: %s -> Unknown (best %.2f)", lbl, score)
                print(c("dim", f"    {lbl} -> Unknown (best {score:.2f})"))
        except Exception as ex:
            SESSION_LOG.warning("Speaker ID failed for %s: %s", lbl, ex)
    return result

def prompt_unknown_speakers(segments, turns, spk_names, audio_np, diar_sr, hf_token):
    """
    For each speaker not in spk_names: show text samples, ask for name,
    optionally enroll in speakers/ database.
    Returns updated spk_names dict.
    """
    unique   = sorted(set(s for _, _, s in turns))
    unknown  = [s for s in unique if s not in spk_names]
    if not unknown:
        return spk_names

    # collect up to 3 text snippets per unknown speaker
    samples = {s: [] for s in unknown}
    for seg in segments:
        txt = seg.text.strip()
        if not txt:
            continue
        spk = assign_speaker(seg.start, seg.end, turns)
        if spk in samples and len(samples[spk]) < 3:
            samples[spk].append((seg.start, txt))

    updated = dict(spk_names)
    to_enroll = {}
    sep = c("dim", "  " + "-" * 54)

    print()
    print(c("bold", "  Unidentified speakers:"))

    for spk in unknown:
        print()
        print(sep)
        print(f"  {c('cyan', spk)}:")
        if samples[spk]:
            for (ts, txt) in samples[spk]:
                display = txt[:60] + "..." if len(txt) > 60 else txt
                print(c("dim", f"    {format_time(ts)}  \"{display}\""))
        else:
            print(c("dim", "    (no text samples)"))
        print()
        name = input(c("bold", "    Name (Enter to skip): ")).strip()
        if not name:
            continue
        name = base_speaker_name(name)
        updated[spk] = name
        print()
        existing = latest_versioned_file(name)
        if existing is not None:
            print(c("dim", f"    \"{name}\" already in database ({existing.name})."))
            ch = input(c("bold", "    [O]verwrite / [A]dd new version / [S]kip [S]: ")).strip().lower()
            if ch == "o":
                to_enroll[spk] = (name, existing)
            elif ch == "a":
                to_enroll[spk] = (name, None)
        else:
            ch = input(c("bold", f"    Save \"{name}\" to speaker database? [y/N]: ")).strip().lower()
            if ch == "y":
                to_enroll[spk] = (name, None)

    print(sep)

    if not to_enroll:
        return updated

    # enroll newly named speakers
    import torch
    import numpy as _np
    print(c("dim", "  Loading embedding model..."), flush=True)
    infer = _load_embedding_infer(hf_token)

    SPEAKERS_DIR.mkdir(exist_ok=True)
    print()
    for spk, (name, target) in to_enroll.items():
        segs = [(s, e) for s, e, lbl in turns if lbl == spk]
        chunks, total_s = [], 0.0
        for s, e in sorted(segs, key=lambda x: x[1]-x[0], reverse=True):
            if total_s >= 60 or e - s < 0.5:
                continue
            si = int(s * diar_sr)
            ei = min(int(e * diar_sr), audio_np.shape[1])
            chunk = audio_np[:, si:ei]
            if chunk.shape[1] > 0:
                chunks.append(chunk)
                total_s += (e - s)
        if not chunks:
            print(c("yellow", f"  {name}: no audio segments - skipped"))
            continue
        try:
            combined = _np.concatenate(chunks, axis=1)
            audio_in = {"waveform": torch.from_numpy(combined), "sample_rate": diar_sr}
            emb      = _np.array(infer(audio_in)).flatten()
            if target is not None:
                out_path, vname, action = target, target.stem, "Overwritten"
            else:
                vname    = next_versioned_name(name)
                out_path = SPEAKERS_DIR / f"{vname}.npy"
                action   = "Enrolled"
            _np.save(str(out_path), emb)
            print(c("green", f"  {action}: {vname} ({emb.shape[0]}d)"))
            SESSION_LOG.info("%s speaker from session: %s", action, vname)
        except Exception as ex:
            print(c("yellow", f"  {name}: enrollment failed - {ex}"))
            SESSION_LOG.warning("Enrollment failed for %s: %s", name, ex)

    return updated

def assign_speaker(seg_start, seg_end, turns):
    best, best_overlap = "?", 0
    for (t_start, t_end, speaker) in turns:
        overlap = min(seg_end, t_end) - max(seg_start, t_start)
        if overlap > best_overlap:
            best_overlap = overlap
            best = speaker
    return best

def find_ffmpeg():
    if shutil.which("ffprobe"):
        return "ffprobe", "ffmpeg"
    for base in [
        r"C:\ffmpeg\bin",
        r"C:\Program Files\ffmpeg\bin",
        r"C:\Program Files (x86)\ffmpeg\bin",
    ]:
        fp = os.path.join(base, "ffprobe.exe")
        ff = os.path.join(base, "ffmpeg.exe")
        if os.path.exists(fp):
            return fp, ff
    return None, None

FFPROBE, FFMPEG = find_ffmpeg()

def get_duration(path):
    if not FFPROBE:
        return 0
    try:
        result = subprocess.run(
            [FFPROBE, "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True
        )
        return float(result.stdout.strip())
    except Exception:
        return 0

def format_duration(secs):
    h, r = divmod(int(secs), 3600)
    m, s = divmod(r, 60)
    if h > 0:
        return f"{h}h {m:02d}m {s:02d}s"
    elif m > 0:
        return f"{m}m {s:02d}s"
    return f"{s}s"

def format_time(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

def _read_key():
    """Read one keypress. Returns 'up'/'down'/'enter'/'quit', 'digit:N', or None.
    Windows uses msvcrt; POSIX uses termios. Ctrl+C raises KeyboardInterrupt."""
    if os.name == "nt":
        import msvcrt
        ch = msvcrt.getch()
        if ch in (b"\x00", b"\xe0"):            # arrow / function-key prefix
            return {b"H": "up", b"P": "down"}.get(msvcrt.getch())
        if ch in (b"\r", b"\n"):
            return "enter"
        if ch == b"\x03":
            raise KeyboardInterrupt
        if ch in (b"q", b"Q"):
            return "quit"
        if ch.isdigit():
            return "digit:" + ch.decode("ascii")
        return None
    import termios, tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":                        # ESC sequence (arrow keys)
            return {"[A": "up", "[B": "down"}.get(sys.stdin.read(2))
        if ch in ("\r", "\n"):
            return "enter"
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch in ("q", "Q"):
            return "quit"
        if ch.isdigit():
            return "digit:" + ch
        return None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _menu_interactive(items, default, allow_quit):
    """Arrow-key menu rendered in place. Returns index, or 'quit'."""
    idx = default if 0 <= default < len(items) else 0
    n = len(items)

    def render():
        sys.stdout.write(f"\033[{n}A")              # back to the top of the block
        for i, it in enumerate(items):
            sys.stdout.write("\033[2K")             # clear the line
            if i == idx:
                sys.stdout.write("  " + c("cyan", "› ") + c("bold", it) + "\n")
            else:
                sys.stdout.write("    " + c("dim", it) + "\n")
        sys.stdout.flush()

    hint = "↑/↓ move, Enter select" + (", q exit" if allow_quit else "")
    print(c("dim", "  (" + hint + ")"))
    for _ in range(n):
        print()                                     # reserve the block
    while True:
        render()
        key = _read_key()
        if key == "up":
            idx = (idx - 1) % n
        elif key == "down":
            idx = (idx + 1) % n
        elif key == "enter":
            return idx
        elif key == "quit" and allow_quit:
            return "quit"
        elif key and key.startswith("digit:"):
            d = int(key.split(":")[1])
            if 1 <= d <= n:
                idx = d - 1
                render()
                return idx


def select_menu(items, default=0, prompt="Select", allow_quit=False):
    """Single-choice selector.

    On an interactive terminal it shows an arrow-key menu (Up/Down to move,
    Enter to select, a digit to jump, optional q to exit) with the current row
    highlighted. When stdin/stdout is not a TTY (piped, redirected) or the
    platform key reader is unavailable, it falls back to a numbered list read
    with input(), so scripted/piped runs keep working. Returns the chosen index
    (0-based), or the string 'quit' when allow_quit and the user exits.
    """
    if sys.stdin.isatty() and sys.stdout.isatty():
        try:
            return _menu_interactive(items, default, allow_quit)
        except Exception:
            pass  # any terminal/key error -> numbered fallback below

    for i, it in enumerate(items, 1):
        marker = c("green", "  [default]") if i - 1 == default else ""
        print(f"  {c('cyan', str(i))}.  {it}{marker}")
    qhint = ", q=exit" if allow_quit else ""
    while True:
        raw = input(c("bold", f"  {prompt} [{default + 1}{qhint}]: ")).strip().lower()
        if raw == "":
            return default
        if allow_quit and raw in ("q", "quit", "exit"):
            return "quit"
        if raw.isdigit() and 1 <= int(raw) <= len(items):
            return int(raw) - 1
        print(c("yellow", "  Invalid choice."))


def pick_files():
    files = sorted([f for f in INPUT_DIR.iterdir()
                    if f.is_file() and f.suffix.lower() in SUPPORTED])
    if not files:
        print(c("red",  "  Folder input/ is empty or no supported files."))
        print(c("dim",  f"  Supported: {', '.join(SUPPORTED)}"))
        input("\n  [Enter] to exit...")
        sys.exit(0)

    print(c("bold", "\n  Files in input/:\n"))
    items = []
    for f in files:
        dur     = get_duration(f)
        dur_str = f", {format_duration(dur)}" if dur else ""
        size_mb = f.stat().st_size / 1024 / 1024
        items.append(f"{f.name}  ({size_mb:.1f} MB{dur_str})")
    items.append("Process all files")
    items.append("Exit")

    idx = select_menu(items, default=0, prompt="Select", allow_quit=True)
    if idx == "quit" or idx == len(items) - 1:        # Exit
        print(c("dim", "\n  Bye."))
        sys.exit(0)
    if idx == len(items) - 2:                          # Process all files
        return files
    return [files[idx]]

def pick_model():
    available = []
    if MODELS_DIR.exists():
        for d in MODELS_DIR.iterdir():
            if d.is_dir() and (d / "model.bin").exists():
                available.append(d.name)

    print(c("bold", "\n  Recognition model:\n"))
    options = []
    if "large-v3-turbo" in available:
        options.append(("large-v3-turbo", "large-v3-turbo (fast + accurate, ~1.6GB)"))
    if "large-v3" in available:
        options.append(("large-v3", "large-v3       (best accuracy, ~3GB, slower)"))
    if "medium" in available:
        options.append(("medium",   "medium         (faster, ~1.5GB)"))
    if "small" in available:
        options.append(("small",    "small          (fastest, ~480MB)"))

    if not options:
        print(c("yellow", "  Warning: no local models found."))
        print(c("dim",    "  Run download_models.py to cache models locally.\n"))
        options.append(("large-v3", "large-v3  (attempt download from HuggingFace)"))

    idx = select_menu([label for _, label in options], default=0, prompt="Model")
    return options[idx][0]

def pick_language():
    langs = [
        ("uk", "Ukrainian"),
        ("",   "Auto-detect (other languages)"),
    ]
    print(c("bold", "\n  Audio language:\n"))
    idx = select_menu([name for _, name in langs], default=0, prompt="Language")
    return langs[idx][0] or None

def pick_diarization():
    print(c("bold", "\n  Speaker diarization:\n"))
    if has_diarization():
        hf_token = os.environ.get("HF_TOKEN", "")
        if not hf_token:
            print(c("yellow", "  torch and pyannote found, but HF_TOKEN not set."))
            print(c("dim",    "  Get token: https://hf.co/settings/tokens"))
            print()
            hf_token = input(c("bold", "  Paste HF_TOKEN (or Enter to skip): ")).strip()
            if hf_token:
                os.environ["HF_TOKEN"] = hf_token
        if hf_token:
            if not diar_models_cached():
                print(c("yellow", "  Note: pyannote model not cached locally."))
                print(c("dim",    "  Diarization will try to download from HuggingFace on first run"))
                print(c("dim",    "  (needs internet + accepted license). Run download_models.py to cache it."))
                print()
            idx = select_menu(
                ["Yes - split by speaker", "No - text with timestamps only"],
                default=0, prompt="Diarization")
            if idx != 0:
                return False, None, None
            print()
            raw = input(c("bold", "  Speaker count (Enter = auto): ")).strip()
            num_speakers = None
            if raw.isdigit() and int(raw) > 0:
                num_speakers = int(raw)
            return True, hf_token, num_speakers
        else:
            print(c("dim", "  No token - diarization unavailable."))
            return False, None, None
    else:
        print(c("yellow", "  Unavailable: need torch + pyannote.audio"))
        print(c("dim",    "  pip install torch --index-url https://download.pytorch.org/whl/cpu"))
        print(c("dim",    "  pip install pyannote.audio"))
        print()
        input(c("dim", "  [Enter] continue without diarization..."))
        return False, None, None

def transcribe_file(file_path, model_size, language, total_dur):
    from faster_whisper import WhisperModel

    model_path_local = MODELS_DIR / model_size
    if model_path_local.exists() and any(model_path_local.iterdir()):
        model_path = str(model_path_local)
    else:
        model_path = model_size

    device, compute_type, device_name = _detect_device()
    device_label = c("cyan", device_name) if device == "cuda" else c("dim", device_name)
    print(c("bold", f"\n  Loading model {model_size}..."), end="", flush=True)
    model = WhisperModel(model_path, device=device, compute_type=compute_type,
                         download_root=str(model_path_local))
    print(c("green", " done") + f"  [{device_label}]")

    tmp_dir = tempfile.mkdtemp()
    try:
        print(c("bold", "  Converting to WAV..."), end="", flush=True)
        # ffmpeg/ffprobe on Windows can fail to open files with non-ASCII names
        # (e.g. Cyrillic), so run them on an ASCII-named copy/hardlink instead.
        src = os.path.join(tmp_dir, "source" + file_path.suffix.lower())
        try:
            os.link(str(file_path), src)            # instant; no extra space (same volume)
        except OSError:
            shutil.copyfile(str(file_path), src)    # fallback (e.g. across volumes)
        wav = os.path.join(tmp_dir, "audio.wav")
        result = subprocess.run(
            [FFMPEG or "ffmpeg", "-y", "-i", src,
             "-ar", "16000", "-ac", "1", "-f", "wav", wav],
            capture_output=True
        )
        if result.returncode != 0 or not os.path.exists(wav):
            tail = (result.stderr or b"").decode("utf-8", "replace").strip().splitlines()
            reason = "  ".join(tail[-3:]) if tail else "unknown ffmpeg error"
            raise RuntimeError("ffmpeg could not convert the file. " + reason)
        print(c("green", " done"))

        # Duration can be 0 if ffprobe could not read the original path; recompute
        # from the ASCII copy so the progress bar and ETA work.
        dur = total_dur if (total_dur and total_dur > 0) else get_duration(src)

        lang_map_short = {"uk": "ukr", None: "auto"}
        lang_hint = lang_map_short.get(language, "auto")
        print(c("bold",  "  Transcription:\n"))
        print(c("dim", f"  Language: {lang_hint}  |  Duration: {format_duration(dur)}\n"))

        _PROG[0] = 0

        segments, info = model.transcribe(
            wav,
            language=language,
            beam_size=5,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
            word_timestamps=False
        )

        results = []
        last_ts = -30
        lines   = []
        t0_transcribe = time.time()

        for seg in segments:
            results.append(seg)
            progress_bar(seg.end, dur, label=seg.text.strip()[:58], start_time=t0_transcribe)
            if seg.start - last_ts >= 30:
                lines.append(f"\n{format_time(seg.start)}")
                last_ts = seg.start
            text = seg.text.strip()
            if text:
                lines.append(text)

        progress_bar(dur, dur, label="done")
        _PROG[0] = 0
        print()

        detected = info.language
        lang_map_full = {"uk": "Ukrainian", "en": "English"}
        print(c("dim", f"\n  Detected: {lang_map_full.get(detected, detected)} "
                       f"({info.language_probability:.0%})"))
        SESSION_LOG.info("Transcription: lang=%s confidence=%.0f%%  segments=%d",
                         detected, info.language_probability * 100, len(results))

        return results, "\n".join(lines).strip(), wav, tmp_dir
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

def save_result(file_path, text):
    OUTPUT_DIR.mkdir(exist_ok=True)
    out = OUTPUT_DIR / (file_path.stem + ".txt")
    out.write_text(text, encoding="utf-8")
    return out

def ensure_workdirs():
    """Create the local working folders if any are missing.

    All of these are git-ignored, so a fresh clone has none of them. Creating
    them up front means running run.bat directly (without setup.bat first)
    works instead of crashing on a missing input/ folder.
    """
    for d in (INPUT_DIR, OUTPUT_DIR, MODELS_DIR, SPEAKERS_DIR, LOGS_DIR):
        d.mkdir(parents=True, exist_ok=True)

def main():
    ensure_workdirs()
    header()
    check_deps()

    if not FFPROBE:
        print(c("yellow", "  Warning: ffmpeg/ffprobe not found in PATH."))
        print(c("dim",    "  Download: https://www.gyan.dev/ffmpeg/builds/"))
        print(c("dim",    r"  Extract and add bin\ to PATH, or copy to C:\ffmpeg\bin\\"))
        print()
        input("  [Enter] to continue without duration info...")
        print()

    files             = pick_files()
    model             = pick_model()
    lang              = pick_language()
    use_diarize, hft, num_spk = pick_diarization()

    print(c("cyan", "\n" + "=" * 60))
    print(c("bold", f"  Processing {len(files)} file(s)...\n"))

    _set_sleep_prevention(True)
    SESSION_LOG.info("Sleep prevention: enabled")

    try:
        for i, f in enumerate(files, 1):
            dur     = get_duration(f)
            size_mb = f.stat().st_size / 1024 / 1024
            print(c("bold", f"  [{i}/{len(files)}] {f.name}"))
            print(c("dim",  f"  Size: {size_mb:.1f} MB  |  Duration: {format_duration(dur)}"))
            SESSION_LOG.info("--- File %d/%d: %s  %.1fMB  %s  model=%s  lang=%s  diarize=%s",
                             i, len(files), f.name, size_mb, format_duration(dur),
                             model, lang or "auto", use_diarize)

            existing_out = OUTPUT_DIR / (f.stem + ".txt")
            if existing_out.exists():
                print(c("yellow", f"  Already transcribed: output/{existing_out.name}"))
                ans = input(c("bold", "  Re-transcribe and overwrite? [y/N]: ")).strip().lower()
                if ans not in ("y", "yes"):
                    print(c("dim", "  Skipped.\n"))
                    SESSION_LOG.info("Skipped (already transcribed): %s", f.name)
                    continue

            t_start = time.time()
            tmp_dir = None
            try:
                segments, text_plain, wav_tmp, tmp_dir = transcribe_file(f, model, lang, dur)

                if use_diarize and hft:
                    try:
                        turns, audio_np, diar_sr = diarize(wav_tmp, hft, num_speakers=num_spk)
                        spk_db    = load_speaker_db()
                        spk_names = {}
                        if spk_db:
                            SESSION_LOG.info("Speaker DB: %d enrolled speaker(s)", len(spk_db))
                            try:
                                spk_names = extract_speaker_embeddings(
                                    audio_np, diar_sr, turns, hft, spk_db)
                            except Exception as _ide:
                                SESSION_LOG.warning("Speaker ID error: %s", _ide)
                        spk_names = prompt_unknown_speakers(
                            segments, turns, spk_names, audio_np, diar_sr, hft)
                        lines    = []
                        prev_spk = None
                        spk_map  = {}
                        for seg in segments:
                            spk = assign_speaker(seg.start, seg.end, turns)
                            if spk not in spk_map:
                                spk_map[spk] = spk_names.get(spk, f"Speaker {len(spk_map)+1}")
                            label = spk_map[spk]
                            txt   = seg.text.strip()
                            if not txt:
                                continue
                            if label != prev_spk:
                                lines.append(f"\n[{label}] {format_time(seg.start)}")
                                prev_spk = label
                            lines.append(txt)
                        text = "\n".join(lines).strip()
                        SESSION_LOG.info("Diarization: %d speakers", len(spk_map))
                    except Exception as de:
                        import traceback as _tb
                        err_str = str(de)
                        SESSION_LOG.warning("Diarization failed: %s\n%s",
                                            err_str, _tb.format_exc())
                        if "401" in err_str or "403" in err_str or \
                           "gated" in err_str.lower() or "restricted" in err_str.lower():
                            print(c("yellow", "\n  Diarization: access error."))
                            print(c("dim",    "  Accept terms on HF (once):"))
                            print(c("dim",    "    https://hf.co/pyannote/speaker-diarization-3.1"))
                            print(c("dim",    "    https://hf.co/pyannote/segmentation-3.0"))
                        else:
                            print(c("yellow", f"\n  Diarization failed: {de}"))
                        print(c("dim", "  Saved without speaker separation."))
                        text = text_plain
                else:
                    text = text_plain

                out     = save_result(f, text)
                elapsed = time.time() - t_start
                print(c("green", f"\n  Saved: {out.name}"))
                print(c("dim",   f"  Processing time: {format_duration(elapsed)}\n"))
                SESSION_LOG.info("Done: %s  elapsed=%s", out.name, format_duration(elapsed))

            except Exception as e:
                import traceback
                elapsed = time.time() - t_start
                print(c("red", f"\n  Error: {e}"))
                SESSION_LOG.error("Failed: %s  elapsed=%s\n%s",
                                  f.name, format_duration(elapsed),
                                  traceback.format_exc())
                print()
            finally:
                if tmp_dir:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
    finally:
        _set_sleep_prevention(False)
        SESSION_LOG.info("Sleep prevention: disabled")

    SESSION_LOG.info("=== Session finished ===")
    print(c("cyan", "=" * 60))
    print(c("bold", c("green", "  Done! Results saved to output/")))
    print(c("dim", f"  Session log: logs/{SESSION_LOG_PATH.name}"))
    print()
    input("  [Enter] to exit...")

if __name__ == "__main__":
    main()
