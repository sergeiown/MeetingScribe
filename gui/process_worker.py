"""The transcription/diarization pipeline, run in a separate OS process.

Not a thread: pyannote.audio drags in matplotlib, which auto-selects a Qt
backend when it detects PySide6 already loaded, crashing Qt6Core.dll after
each transcription. MPLBACKEND=Agg doesn't reliably fix it - a process that
never imports PySide6 avoids the conflict entirely."""

import shutil
import threading

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


_MIN_AVAILABLE_GB_FOR_PARALLEL = 5.0


def _available_memory_gb():
    """Physical memory Windows could hand out right now (free plus cache), or None."""
    try:
        import ctypes

        class _MemoryStatus(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(_MemoryStatus)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return status.ullAvailPhys / (1 << 30)
    except Exception:
        return None


def run_transcription_process(files, model_size, language, auto_diarize, hf_token,
                               num_speakers, device_preference, allow_parallel_diarization,
                               progress_q, decision_q, cancel_event):
    """Entry point for the child process; must stay module-level so
    multiprocessing can pickle it under Windows' spawn method. Never
    imports gui/PySide6.

    Saves the transcript + segments sidecar first; only continues into
    diarization if auto_diarize and a token are present, otherwise the file
    stays eligible for a later run_diarize_process pass."""
    progress_q.put(("status", "Starting recognition engine..."))
    import core

    # Spawned process = fresh interpreter; needs its own init_logger() or nothing reaches logs/session.log.
    core.init_logger()
    # Importing the whole ML stack just to log versions took ~10 s, all of it
    # before the user saw anything - so it runs alongside the real work instead.
    threading.Thread(target=core.log_ml_versions, daemon=True).start()

    cancel_token = ProcessCancelToken(cancel_event)

    def on_status(msg):
        progress_q.put(("status", msg))

    def on_transcribe_progress(cur, total, label):
        # label is the live segment text (real transcript content).
        progress_q.put(("progress", "transcribe", cur, total, label))

    def on_diarize_progress(cur, total, label):
        # label is an internal pyannote stage name, not transcript content.
        progress_q.put(("progress", "diarize", cur, total, label))

    def decide(label, samples, index, total):
        req = SpeakerDecisionRequest(label, samples, decision_q)
        progress_q.put(("speaker_decision_needed", label, samples, index, total))
        return decision_q.get()  # blocks this process only, until the GUI answers

    # Speaker analysis is independent of the transcript, so when recognition
    # runs on the GPU it can use the otherwise idle CPU at the same time.
    # On a CPU-only recognition run the two would just compete for cores.
    parallel_diarization = bool(auto_diarize and hf_token and core.uses_vulkan(model_size, device_preference))
    if parallel_diarization:
        available = _available_memory_gb()
        if not allow_parallel_diarization:
            parallel_diarization = False
            on_status("Recording in progress - speaker analysis will start after recognition")
        elif available is not None and available < _MIN_AVAILABLE_GB_FOR_PARALLEL:
            parallel_diarization = False
            on_status("Low free memory - speaker analysis will start after recognition")

    try:
        for i, f in enumerate(files, 1):
            progress_q.put(("file_started", f.name, i, len(files)))
            tmp_dir = None
            background = None
            try:
                cancel_token.check()
                prepared = None
                if parallel_diarization:
                    on_status("Converting to WAV...")
                    prepared = core.convert_to_wav(f)
                    tmp_dir = prepared[1]
                    background = _BackgroundDiarization(
                        core, prepared[0], hf_token, num_speakers, device_preference, cancel_token)
                    on_status("Speaker analysis runs alongside recognition...")
                result = core.transcribe_file(
                    f, model_size, language, core.get_duration(f), device_preference=device_preference,
                    cancel_token=cancel_token, on_status=on_status, on_progress=on_transcribe_progress,
                    prepared_wav=prepared)
                tmp_dir = result.tmp_dir
                text = result.text
                diarized = False
                diar_error_msg = None

                if auto_diarize and hf_token:
                    try:
                        if background is not None:
                            dr = background.wait(cancel_token, on_status, on_diarize_progress)
                            text = _label_with_diarization(core, dr, result.segments, hf_token, cancel_token,
                                                           on_status, decide)
                        else:
                            text = _diarize_and_label(core, result.wav_path, result.segments, hf_token, num_speakers,
                                                      device_preference, cancel_token, on_status, on_diarize_progress,
                                                      decide)
                        diarized = True
                    except core.Cancelled:
                        # Recognition already finished: keep it, so the file shows as
                        # transcribed (and can be diarized later) instead of losing it.
                        out = core.save_result(f, result.text)
                        core.save_segments(f, result.segments, diarized=False)
                        progress_q.put(("file_done", f.name, str(out)))
                        raise
                    except Exception as de:
                        diar_error_msg = _diarization_error_message(de)

                out = core.save_result(f, text)
                core.save_segments(f, result.segments, diarized=diarized)
                progress_q.put(("file_done", f.name, str(out)))
                # Sent *after* file_done, not via on_status before it: the GUI
                # wipes/replaces its live status trail with the clean saved
                # transcript as soon as file_done arrives, which would erase
                # this message unseen if it came any earlier.
                if diar_error_msg:
                    on_status(diar_error_msg)
            except core.Cancelled:
                progress_q.put(("cancelled",))
                return
            except core.MediaError as me:
                progress_q.put(("file_failed", f.name, f"Skipped: {me}."))
            except Exception as e:
                progress_q.put(("file_failed", f.name, str(e)))
            finally:
                if background is not None:
                    background.abort()
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
    threading.Thread(target=core.log_ml_versions, daemon=True).start()
    cancel_token = ProcessCancelToken(cancel_event)

    def on_status(msg):
        progress_q.put(("status", msg))

    def on_diarize_progress(cur, total, label):
        progress_q.put(("progress", "diarize", cur, total, label))

    def decide(label, samples, index, total):
        req = SpeakerDecisionRequest(label, samples, decision_q)
        progress_q.put(("speaker_decision_needed", label, samples, index, total))
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


class _BackgroundDiarization:
    """core.diarize on a worker thread. Its status/progress are kept, not
    forwarded, until wait(): during recognition the GUI shows only that."""

    def __init__(self, core, wav_path, hf_token, num_speakers, device_preference, cancel_token):
        self.result = None
        self.error = None
        self._status = ""
        self._progress = None
        self._thread = threading.Thread(
            target=self._run, args=(core, wav_path, hf_token, num_speakers, device_preference, cancel_token),
            daemon=True)
        self._thread.start()

    def _run(self, core, wav_path, hf_token, num_speakers, device_preference, cancel_token):
        try:
            self.result = core.diarize(
                wav_path, hf_token, num_speakers, device_preference=device_preference,
                cancel_token=cancel_token, on_status=self._set_status, on_progress=self._set_progress)
        except BaseException as e:
            self.error = e

    def _set_status(self, msg):
        self._status = msg

    def _set_progress(self, cur, total, label):
        self._progress = (cur, total, label)

    def wait(self, cancel_token, on_status, on_progress):
        if self._thread.is_alive() and self._status:
            on_status(self._status)
        while self._thread.is_alive():
            self._thread.join(0.5)
            if self._progress is not None:
                on_progress(*self._progress)
            cancel_token.check()
        if self.error is not None:
            raise self.error
        return self.result

    def abort(self):
        self._thread.join(timeout=10)


def _diarize_and_label(core, wav_path, segments, hf_token, num_speakers, device_preference,
                        cancel_token, on_status, on_progress, decide):
    dr = core.diarize(
        wav_path, hf_token, num_speakers, device_preference=device_preference,
        cancel_token=cancel_token, on_status=on_status, on_progress=on_progress)
    return _label_with_diarization(core, dr, segments, hf_token, cancel_token, on_status, decide)


def _label_with_diarization(core, dr, segments, hf_token, cancel_token, on_status, decide):
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
        progress_q.put(("failed", _model_download_error_message(e)))


def _model_download_error_message(e):
    err_str = str(e)
    if "401" in err_str:
        return "Invalid Hugging Face token."
    if "403" in err_str or "gated" in err_str.lower() or "restricted" in err_str.lower():
        return "Access denied - accept the license on Hugging Face."
    return err_str


def _diarization_error_message(de):
    err_str = str(de)
    if any(s in err_str for s in ("401", "403")) or "gated" in err_str.lower() or "restricted" in err_str.lower():
        return ("Diarization: access error - accept terms on HF "
                "(https://hf.co/pyannote/speaker-diarization-3.1, "
                "https://hf.co/pyannote/segmentation-3.0). Saved without speaker separation.")
    return f"Diarization failed: {de}. Saved without speaker separation."
