"""The transcription/diarization pipeline, run in a separate OS process.

Not a thread: pyannote.audio drags in matplotlib, which auto-selects a Qt
backend when it detects PySide6 already loaded, crashing Qt6Core.dll after
each transcription. MPLBACKEND=Agg doesn't reliably fix it - a process that
never imports PySide6 avoids the conflict entirely."""

import shutil

from .decision_request import SpeakerDecisionRequest


class ProcessCancelToken:
    """core.CancelToken-compatible wrapper around a multiprocessing.Event -
    threading.Event can't cross a process boundary, so this substitutes it
    without core needing to know which one it's holding."""

    def __init__(self, event):
        self._event = event

    def cancel(self):
        self._event.set()

    def is_cancelled(self):
        return self._event.is_set()

    def check(self):
        if self._event.is_set():
            import core
            raise core.Cancelled()


def run_transcription_process(files, model_size, language, auto_diarize, hf_token,
                               num_speakers, device_preference, progress_q, decision_q, cancel_event):
    """Entry point for the child process; must stay module-level so
    multiprocessing can pickle it under Windows' spawn method. Never
    imports gui/PySide6.

    Saves the transcript + segments sidecar first; only continues into
    diarization if auto_diarize and a token are present, otherwise the file
    stays eligible for a later run_diarize_process pass."""
    import core

    # Spawned process = fresh interpreter; needs its own init_logger() or nothing reaches logs/session.log.
    core.init_logger()
    core.log_ml_versions()

    cancel_token = ProcessCancelToken(cancel_event)

    def on_status(msg):
        progress_q.put(("status", msg))

    def on_transcribe_progress(cur, total, label):
        # label is the live segment text (real transcript content).
        progress_q.put(("progress", "transcribe", cur, total, label))

    def on_diarize_progress(cur, total, label):
        # label is an internal pyannote stage name, not transcript content.
        progress_q.put(("progress", "diarize", cur, total, label))

    def decide(label, samples):
        req = SpeakerDecisionRequest(label, samples, decision_q)
        progress_q.put(("speaker_decision_needed", label, samples))
        return decision_q.get()  # blocks this process only, until the GUI answers

    try:
        for i, f in enumerate(files, 1):
            progress_q.put(("file_started", f.name, i, len(files)))
            tmp_dir = None
            try:
                cancel_token.check()
                result = core.transcribe_file(
                    f, model_size, language, core.get_duration(f), device_preference=device_preference,
                    cancel_token=cancel_token, on_status=on_status, on_progress=on_transcribe_progress)
                tmp_dir = result.tmp_dir
                text = result.text
                diarized = False

                if auto_diarize and hf_token:
                    try:
                        text = _diarize_and_label(core, result.wav_path, result.segments, hf_token, num_speakers,
                                                  device_preference, cancel_token, on_status, on_diarize_progress, decide)
                        diarized = True
                    except core.Cancelled:
                        raise
                    except Exception as de:
                        on_status(_diarization_error_message(de))

                out = core.save_result(f, text)
                core.save_segments(f, result.segments, diarized=diarized)
                progress_q.put(("file_done", f.name, str(out)))
            except core.Cancelled:
                progress_q.put(("cancelled",))
                return
            except core.MediaError as me:
                progress_q.put(("file_failed", f.name, f"Skipped: {me}."))
            except Exception as e:
                progress_q.put(("file_failed", f.name, str(e)))
            finally:
                if tmp_dir:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
        progress_q.put(("finished",))
    except Exception as e:
        progress_q.put(("file_failed", "", f"Worker process error: {e}"))
        progress_q.put(("finished",))


def run_diarize_process(files, hf_token, num_speakers, device_preference, progress_q, decision_q, cancel_event):
    """Entry point for a standalone "Identify speakers" run: reloads the
    transcript segments saved by a previous, separate transcription pass and
    re-converts the source file to WAV, so no Whisper re-run is needed."""
    import core

    core.init_logger()
    core.log_ml_versions()
    cancel_token = ProcessCancelToken(cancel_event)

    def on_status(msg):
        progress_q.put(("status", msg))

    def on_diarize_progress(cur, total, label):
        progress_q.put(("progress", "diarize", cur, total, label))

    def decide(label, samples):
        req = SpeakerDecisionRequest(label, samples, decision_q)
        progress_q.put(("speaker_decision_needed", label, samples))
        return decision_q.get()

    try:
        for i, f in enumerate(files, 1):
            progress_q.put(("file_started", f.name, i, len(files)))
            tmp_dir = None
            try:
                cancel_token.check()
                data = core.load_segments(f)
                if not data or data.get("diarized"):
                    progress_q.put(("file_failed", f.name, "Skipped: nothing pending - run recognition first."))
                    continue
                segments = [core.Segment(**s) for s in data["segments"]]

                wav_path, tmp_dir = core.convert_to_wav(f)
                text = _diarize_and_label(core, wav_path, segments, hf_token, num_speakers, device_preference,
                                          cancel_token, on_status, on_diarize_progress, decide)
                out = core.save_result(f, text)
                core.save_segments(f, segments, diarized=True)
                progress_q.put(("file_done", f.name, str(out)))
            except core.Cancelled:
                progress_q.put(("cancelled",))
                return
            except core.MediaError as me:
                progress_q.put(("file_failed", f.name, f"Skipped: {me}."))
            except Exception as e:
                progress_q.put(("file_failed", f.name, _diarization_error_message(e)))
            finally:
                if tmp_dir:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
        progress_q.put(("finished",))
    except Exception as e:
        progress_q.put(("file_failed", "", f"Worker process error: {e}"))
        progress_q.put(("finished",))


def _diarize_and_label(core, wav_path, segments, hf_token, num_speakers, device_preference,
                        cancel_token, on_status, on_progress, decide):
    dr = core.diarize(
        wav_path, hf_token, num_speakers, device_preference=device_preference,
        cancel_token=cancel_token, on_status=on_status, on_progress=on_progress)
    spk_db = core.load_speaker_db()
    spk_names = {}
    if spk_db:
        spk_names = core.extract_speaker_embeddings(
            dr.audio, dr.sr, dr.turns, hf_token, spk_db, on_status=on_status)
    spk_names = core.resolve_unknown_speakers(
        segments, dr.turns, spk_names, dr.audio, dr.sr, hf_token,
        decide=decide, cancel_token=cancel_token, on_status=on_status)

    lines, prev_spk, spk_map = [], None, {}
    for seg in segments:
        spk = core.assign_speaker(seg.start, seg.end, dr.turns)
        if spk not in spk_map:
            spk_map[spk] = spk_names.get(spk, f"Speaker {len(spk_map) + 1}")
        label = spk_map[spk]
        txt = seg.text.strip()
        if not txt:
            continue
        if label != prev_spk:
            lines.append(f"\n[{label}] {core.format_time(seg.start)}")
            prev_spk = label
        lines.append(txt)
    return "\n".join(lines).strip()


def run_model_download_process(kind, args, hf_token, progress_q, cancel_event):
    """Entry point for a model download's child process. huggingface_hub's
    snapshot_download() has no cancel hook, so a Stop click can only ever
    really take effect by killing this whole process (see
    ModelDownloadProcessWorker.cancel() in workers.py) - cancel_event is
    still checked between files for a faster stop when the timing lines up."""
    import core

    cancel_token = ProcessCancelToken(cancel_event)

    def on_progress(cur, total, label):
        progress_q.put(("progress", cur, total, label))

    try:
        if kind == "whisper":
            key, hf_repo = args
            core.download_whisper_model(key, hf_repo, hf_token, cancel_token=cancel_token, on_progress=on_progress)
        else:
            core.download_diarization_models(hf_token, cancel_token=cancel_token, on_progress=on_progress)
        progress_q.put(("finished",))
    except core.Cancelled:
        progress_q.put(("cancelled",))
    except Exception as e:
        progress_q.put(("failed", str(e)))


def _diarization_error_message(de):
    err_str = str(de)
    if any(s in err_str for s in ("401", "403")) or "gated" in err_str.lower() or "restricted" in err_str.lower():
        return ("Diarization: access error - accept terms on HF "
                "(https://hf.co/pyannote/speaker-diarization-3.1, "
                "https://hf.co/pyannote/segmentation-3.0). Saved without speaker separation.")
    return f"Diarization failed: {de}. Saved without speaker separation."
