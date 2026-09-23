"""Speaker voiceprint database: matching, enrollment, and management."""

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal, Optional

from ._util import silence
from .cancellation import CancelToken, StatusFn
from .device import detect_device
from .paths import SPEAKERS_DIR, MODELS_DIR

_log = logging.getLogger(__name__)

SPEAKER_ID_THRESHOLD = 0.75  # cosine similarity cutoff for a name match

_SPK_VER_RE = re.compile(r"\s*\(v(\d+)\)$")


def base_speaker_name(name: str) -> str:
    """'Віталій Рубан (v2)' -> 'Віталій Рубан'."""
    return _SPK_VER_RE.sub("", name).strip()


def next_versioned_name(name: str, speakers_dir: Path = SPEAKERS_DIR) -> str:
    """Next free version for this person: first time -> (v1), otherwise
    (vN+1). An unversioned existing file counts as v1."""
    base = base_speaker_name(name)
    max_v = 0
    if speakers_dir.exists():
        for f in speakers_dir.glob("*.npy"):
            if base_speaker_name(f.stem) == base:
                m = _SPK_VER_RE.search(f.stem)
                max_v = max(max_v, int(m.group(1)) if m else 1)
    return f"{base} (v{max_v + 1})"


def latest_versioned_file(name: str, speakers_dir: Path = SPEAKERS_DIR) -> Optional[Path]:
    """Path of the highest-version .npy for this person, or None."""
    base = base_speaker_name(name)
    best, best_v = None, -1
    if speakers_dir.exists():
        for f in speakers_dir.glob("*.npy"):
            if base_speaker_name(f.stem) == base:
                m = _SPK_VER_RE.search(f.stem)
                v = int(m.group(1)) if m else 1
                if v > best_v:
                    best_v, best = v, f
    return best


def load_speaker_db(speakers_dir: Path = SPEAKERS_DIR) -> dict:
    """{versioned_stem: np.ndarray} for every .npy under speakers_dir."""
    if not speakers_dir.exists():
        return {}
    import numpy as np
    db = {}
    for f in speakers_dir.glob("*.npy"):
        try:
            db[f.stem] = np.load(str(f))
        except Exception:
            pass
    return db


def _cosine_sim(a, b) -> float:
    import numpy as np
    a = a / (np.linalg.norm(a) + 1e-10)
    b = b / (np.linalg.norm(b) + 1e-10)
    return float(np.dot(a, b))


def identify_speaker(emb, db: dict):
    """Best cosine match in db. Returns (name, score) - name is None if
    below threshold."""
    best_name, best_score = None, -1.0
    for name, ref in db.items():
        score = _cosine_sim(emb, ref)
        if score > best_score:
            best_score = score
            best_name = name
    if best_score >= SPEAKER_ID_THRESHOLD:
        return best_name, best_score
    return None, best_score


def _find_model_dir(base_dir: Path) -> Optional[Path]:
    """Find directory with model weights (root or snapshots/<commit>/)."""
    weight_names = {"pytorch_model.bin", "model.safetensors", "pytorch_model.safetensors"}
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


def _load_embedding_infer(hf_token: str, models_dir: Path = MODELS_DIR,
                           on_status: Optional[StatusFn] = None):
    """Load pyannote/embedding from local cache. Returns an Inference object."""
    import os
    import torch as _torch
    with silence():
        from pyannote.audio import Inference, Model
    device, _, _ = detect_device()
    torch_dev = _torch.device(device)
    emb_base = models_dir / "models--pyannote--embedding"
    emb_dir = _find_model_dir(emb_base) if emb_base.exists() else None
    if emb_dir is not None:
        prev = os.environ.get("HF_HUB_OFFLINE")
        os.environ["HF_HUB_OFFLINE"] = "1"
        try:
            with silence():
                emb_model = Model.from_pretrained(str(emb_dir), token=hf_token)
            with silence():
                emb_model = emb_model.to(torch_dev)
            return Inference(emb_model, window="whole")
        except TypeError:
            with silence():
                emb_model = Model.from_pretrained(str(emb_dir), use_auth_token=hf_token)
            with silence():
                emb_model = emb_model.to(torch_dev)
            return Inference(emb_model, window="whole")
        finally:
            if prev is None:
                os.environ.pop("HF_HUB_OFFLINE", None)
            else:
                os.environ["HF_HUB_OFFLINE"] = prev
    if on_status:
        on_status("Embedding model not cached locally - attempting download...")
    try:
        emb_model = Model.from_pretrained("pyannote/embedding", token=hf_token)
    except TypeError:
        emb_model = Model.from_pretrained("pyannote/embedding", use_auth_token=hf_token)
    return Inference(emb_model, window="whole")


def assign_speaker(seg_start, seg_end, turns) -> str:
    best, best_overlap = "?", 0
    for (t_start, t_end, speaker) in turns:
        overlap = min(seg_end, t_end) - max(seg_start, t_start)
        if overlap > best_overlap:
            best_overlap = overlap
            best = speaker
    return best


def extract_speaker_embeddings(audio_np, sr, turns, hf_token: str, db: dict, *,
                                on_status: Optional[StatusFn] = None,
                                on_match: Optional[Callable[[str, Optional[str], float], None]] = None) -> dict:
    """Matches each unique speaker label's embedding against db. Unmatched
    labels are omitted from the returned dict but still reported via on_match(name=None)."""
    import torch
    import numpy as np
    unique = list(set(s for _, _, s in turns))
    if not unique or not db:
        return {}

    if on_status:
        on_status("Speaker identification...")
    infer = _load_embedding_infer(hf_token, on_status=on_status)

    result = {}
    for lbl in sorted(unique):
        segs = [(s, e) for s, e, sp in turns if sp == lbl]
        chunks, total_s = [], 0.0
        for s, e in sorted(segs, key=lambda x: x[1] - x[0], reverse=True):
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
            combined = np.concatenate(chunks, axis=1)
            audio_in = {"waveform": torch.from_numpy(combined), "sample_rate": sr}
            emb = np.array(infer(audio_in)).flatten()
            name, score = identify_speaker(emb, db)
            display_name = base_speaker_name(name) if name else None
            if on_match:
                on_match(lbl, display_name, score)
            if name:
                _log.info("Speaker ID: %s -> %s (%.2f)", lbl, name, score)
                result[lbl] = display_name
            else:
                _log.info("Speaker ID: %s -> Unknown (best %.2f)", lbl, score)
        except Exception as ex:
            _log.warning("Speaker ID failed for %s: %s", lbl, ex)
    return result


def _distance_to_turns(seg_start, seg_end, label_turns) -> float:
    best = None
    for t_start, t_end in label_turns:
        if seg_end < t_start:
            d = t_start - seg_end
        elif seg_start > t_end:
            d = seg_start - t_end
        else:
            d = 0.0
        if best is None or d < best:
            best = d
    return best


def collect_speaker_samples(segments, turns, unknown_labels, max_samples: int = 3) -> dict:
    """{label: [(start_time, text), ...]} - up to max_samples per label.

    A label can end up with no overlapping segment at all (e.g. a brief
    interjection whisper folded into a neighboring speaker's segment instead
    of splitting it out) - the naming dialog must never show up with nothing
    to go on, so any label left empty here falls back to whichever texted
    segment is closest in time to one of its turns."""
    samples = {lbl: [] for lbl in unknown_labels}
    texted = [(seg.start, seg.end, seg.text.strip()) for seg in segments if seg.text.strip()]

    for start, end, txt in texted:
        spk = assign_speaker(start, end, turns)
        if spk in samples and len(samples[spk]) < max_samples:
            samples[spk].append((start, txt))

    if not texted:
        return samples

    for label in unknown_labels:
        if samples[label]:
            continue
        label_turns = [(s, e) for s, e, lbl in turns if lbl == label]
        if not label_turns:
            continue
        nearest = min(texted, key=lambda seg: _distance_to_turns(seg[0], seg[1], label_turns))
        samples[label].append((nearest[0], nearest[2]))
    return samples


@dataclass
class SpeakerNameChoice:
    label: str
    name: Optional[str]
    action: Literal["new", "overwrite", "add_version", "skip"]
    target_path: Optional[Path] = None  # set only when action == "overwrite"


NamingDecisionFn = Callable[[str, list], "SpeakerNameChoice"]


@dataclass
class EnrollResult:
    path: Path
    versioned_name: str
    action: Literal["Enrolled", "Overwritten"]


def enroll_speaker(infer, name: str, label: str, turns, audio_np, sr, *,
                    target_path: Optional[Path] = None,
                    speakers_dir: Path = SPEAKERS_DIR) -> Optional[EnrollResult]:
    """Extract up to 60s of embedding audio for `label`'s segments and save
    to speakers/{versioned_name}.npy (or overwrite target_path). Returns
    None if the label has no usable audio segments."""
    import torch
    import numpy as np
    segs = [(s, e) for s, e, lbl in turns if lbl == label]
    chunks, total_s = [], 0.0
    for s, e in sorted(segs, key=lambda x: x[1] - x[0], reverse=True):
        if total_s >= 60 or e - s < 0.5:
            continue
        si = int(s * sr)
        ei = min(int(e * sr), audio_np.shape[1])
        chunk = audio_np[:, si:ei]
        if chunk.shape[1] > 0:
            chunks.append(chunk)
            total_s += (e - s)
    if not chunks:
        return None

    combined = np.concatenate(chunks, axis=1)
    audio_in = {"waveform": torch.from_numpy(combined), "sample_rate": sr}
    emb = np.array(infer(audio_in)).flatten()
    speakers_dir.mkdir(parents=True, exist_ok=True)
    if target_path is not None:
        out_path, vname, action = target_path, target_path.stem, "Overwritten"
    else:
        vname = next_versioned_name(name, speakers_dir)
        out_path = speakers_dir / f"{vname}.npy"
        action = "Enrolled"
    np.save(str(out_path), emb)
    return EnrollResult(out_path, vname, action)


def resolve_unknown_speakers(segments, turns, spk_names: dict, audio_np, sr, hf_token: str, *,
                              decide: NamingDecisionFn,
                              cancel_token: Optional[CancelToken] = None,
                              on_status: Optional[StatusFn] = None,
                              speakers_dir: Path = SPEAKERS_DIR) -> dict:
    """Asks `decide` once per unlabeled speaker, then enrolls the results.
    `decide` may answer synchronously (CLI) or via a cross-thread GUI dialog;
    it just returns a SpeakerNameChoice either way. Returns merged {label: name}."""
    unique = sorted(set(s for _, _, s in turns))
    unknown = [s for s in unique if s not in spk_names]
    if not unknown:
        return spk_names

    samples = collect_speaker_samples(segments, turns, unknown)
    updated = dict(spk_names)
    to_enroll = {}

    for label in unknown:
        if cancel_token:
            cancel_token.check()
        choice = decide(label, samples[label])
        if choice.action == "skip" or not choice.name:
            continue
        updated[label] = choice.name
        to_enroll[label] = choice

    if not to_enroll:
        return updated

    if on_status:
        on_status("Loading embedding model...")
    infer = _load_embedding_infer(hf_token, models_dir=MODELS_DIR, on_status=on_status)

    for label, choice in to_enroll.items():
        result = enroll_speaker(infer, choice.name, label, turns, audio_np, sr,
                                 target_path=choice.target_path, speakers_dir=speakers_dir)
        if result:
            _log.info("%s speaker from session: %s", result.action, result.versioned_name)
            if on_status:
                on_status(f"{result.action}: {result.versioned_name}")
        else:
            _log.warning("Enrollment skipped for %s: no usable audio segments", choice.name)
            if on_status:
                on_status(f"{choice.name}: no audio segments - skipped")

    return updated


@dataclass
class SpeakerVersion:
    path: Path
    version: int
    modified: datetime


@dataclass
class SpeakerPerson:
    base_name: str
    versions: list


def list_speaker_persons(speakers_dir: Path = SPEAKERS_DIR) -> list:
    """Group speakers/*.npy by base name, newest version first per person,
    persons sorted alphabetically."""
    if not speakers_dir.exists():
        return []
    people = {}
    for f in speakers_dir.glob("*.npy"):
        base = base_speaker_name(f.stem)
        m = _SPK_VER_RE.search(f.stem)
        version = int(m.group(1)) if m else 1
        modified = datetime.fromtimestamp(f.stat().st_mtime)
        people.setdefault(base, []).append(SpeakerVersion(f, version, modified))
    return [
        SpeakerPerson(base, sorted(versions, key=lambda v: v.version, reverse=True))
        for base, versions in sorted(people.items())
    ]


def delete_speaker_version(path: Path) -> None:
    path.unlink(missing_ok=True)


def delete_speaker_person(base_name: str, speakers_dir: Path = SPEAKERS_DIR) -> int:
    """Delete every version of this person. Returns the count deleted."""
    count = 0
    for f in speakers_dir.glob("*.npy"):
        if base_speaker_name(f.stem) == base_name:
            f.unlink()
            count += 1
    return count


def rename_speaker_person(old_base: str, new_base: str, speakers_dir: Path = SPEAKERS_DIR) -> int:
    """Rename every version file's base name, preserving each file's own
    (vN) suffix, e.g. 'Old Name (v2).npy' -> 'New Name (v2).npy'. Returns
    the count renamed."""
    count = 0
    for f in speakers_dir.glob("*.npy"):
        if base_speaker_name(f.stem) == old_base:
            m = _SPK_VER_RE.search(f.stem)
            suffix = m.group(0) if m else ""
            f.rename(speakers_dir / f"{new_base}{suffix}.npy")
            count += 1
    return count


def export_speakers(paths: Optional[list] = None, dest_zip: Path = None) -> int:
    """Zips voiceprint .npy files into dest_zip, flattening to just the
    filename (no directory structure). paths=None exports every currently
    enrolled voiceprint. Returns how many files were written."""
    import zipfile

    if paths is None:
        paths = [v.path for p in list_speaker_persons() for v in p.versions]
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in paths:
            zf.write(p, arcname=p.name)
    return len(paths)


def import_speakers(path: Path, speakers_dir: Path = SPEAKERS_DIR) -> int:
    """Imports .npy voiceprints from either a single .npy file or a zip made
    by export_speakers(). Never overwrites an existing file - a name
    collision is saved as a new version instead, so import can't destroy an
    enrollment."""
    speakers_dir.mkdir(parents=True, exist_ok=True)
    count = 0

    def save_one(name: str, data: bytes) -> None:
        nonlocal count
        dest = speakers_dir / name
        if dest.exists():
            base = base_speaker_name(Path(name).stem)
            dest = speakers_dir / f"{next_versioned_name(base, speakers_dir)}.npy"
        dest.write_bytes(data)
        count += 1

    if path.suffix.lower() == ".npy":
        save_one(path.name, path.read_bytes())
        return count

    import zipfile

    with zipfile.ZipFile(path, "r") as zf:
        for info in zf.infolist():
            name = Path(info.filename).name
            if not name.lower().endswith(".npy") or info.is_dir():
                continue
            with zf.open(info) as src:
                save_one(name, src.read())
    return count
