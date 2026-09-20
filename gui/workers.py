"""GUI-side controllers for background work.

TranscriptionWorker runs the actual ML pipeline in a separate OS process
(see process_worker.py) and polls a multiprocessing.Queue via a QTimer,
re-emitting the same messages as Qt signals - MainWindow's signal handlers
don't need to know or care that the work happens out-of-process.

ModelDownloadWorker stays QThread-based: it only touches huggingface_hub,
which never pulls in matplotlib/pyannote, so it doesn't hit the PySide6/Qt
conflict that motivated the process-based design above.
"""

import multiprocessing as mp
import queue

from PySide6.QtCore import QObject, QThread, QTimer, Signal

import core


class _QueueProcessWorker(QObject):
    """Shared machinery for running one pipeline stage (transcription or
    diarization) in a separate OS process and re-emitting its progress-queue
    messages as Qt signals. Subclasses set self._target/self._args."""

    status = Signal(str)
    progress = Signal(str, float, float, str)  # phase ("transcribe"/"diarize"), current, total, label
    speaker_decision_needed = Signal(object)
    file_started = Signal(str, int, int)
    file_done = Signal(str, str)
    file_failed = Signal(str, str)
    finished = Signal()
    cancelled = Signal()

    def __init__(self):
        super().__init__()
        self._target = None
        self._args = ()
        self._progress_q = mp.Queue()
        self._decision_q = mp.Queue()
        self._cancel_event = mp.Event()
        self._process = None
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll)

    def cancel(self):
        self._cancel_event.set()

    def run(self):
        self._process = mp.Process(
            target=self._target,
            args=(*self._args, self._progress_q, self._decision_q, self._cancel_event),
            daemon=True,
        )
        self._process.start()
        self._poll_timer.start(100)

    def terminate(self):
        """Forceful stop, used when the window is closing and cooperative
        cancellation hasn't finished in time."""
        self._poll_timer.stop()
        if self._process is not None and self._process.is_alive():
            self._process.terminate()

    def _poll(self):
        try:
            while True:
                self._handle_message(self._progress_q.get_nowait())
        except queue.Empty:
            pass
        if self._process is not None and not self._process.is_alive():
            # The child exited without ever sending finished/cancelled - most
            # likely it crashed. Surface that instead of polling forever.
            code = self._process.exitcode
            if code not in (0, None) and self._poll_timer.isActive():
                self._poll_timer.stop()
                self.file_failed.emit("", f"Worker process exited unexpectedly (code {code}).")
                self.finished.emit()

    def _handle_message(self, msg):
        kind = msg[0]
        if kind == "status":
            self.status.emit(msg[1])
        elif kind == "progress":
            self.progress.emit(msg[1], msg[2], msg[3], msg[4])
        elif kind == "file_started":
            self.file_started.emit(msg[1], msg[2], msg[3])
        elif kind == "file_done":
            self.file_done.emit(msg[1], msg[2])
        elif kind == "file_failed":
            self.file_failed.emit(msg[1], msg[2])
        elif kind == "speaker_decision_needed":
            from .decision_request import SpeakerDecisionRequest
            self.speaker_decision_needed.emit(SpeakerDecisionRequest(msg[1], msg[2], self._decision_q))
        elif kind == "finished":
            self._poll_timer.stop()
            self.finished.emit()
        elif kind == "cancelled":
            self._poll_timer.stop()
            self.cancelled.emit()


class TranscriptionWorker(_QueueProcessWorker):
    def __init__(self, files, model_size, language, auto_diarize, hf_token, num_speakers, device_preference="auto"):
        super().__init__()
        from .process_worker import run_transcription_process
        self._target = run_transcription_process
        self._args = (files, model_size, language, auto_diarize, hf_token, num_speakers, device_preference)


class DiarizationWorker(_QueueProcessWorker):
    def __init__(self, files, hf_token, num_speakers, device_preference="auto"):
        super().__init__()
        from .process_worker import run_diarize_process
        self._target = run_diarize_process
        self._args = (files, hf_token, num_speakers, device_preference)


class ModelDownloadWorker(QObject):
    progress = Signal(float, float, str)
    finished = Signal()
    failed = Signal(str)

    def __init__(self, download_fn):
        """download_fn(cancel_token, on_progress) performs one model's
        download; callers pass a closure over core.models_catalog.download_whisper_model /
        download_pyannote_model with its model-specific args bound."""
        super().__init__()
        self._download_fn = download_fn
        self._cancel_token = core.CancelToken()

    def cancel(self):
        self._cancel_token.cancel()

    def run(self):
        try:
            self._download_fn(self._cancel_token, self.progress.emit)
            self.finished.emit()
        except core.Cancelled:
            self.finished.emit()
        except Exception as e:
            self.failed.emit(str(e))


class UpdateCheckWorker(QObject):
    """Pure network check (GitHub Releases API) - no ML libraries touched,
    safe on a QThread like ModelDownloadWorker."""

    checked = Signal(object)  # {"version": ..., "url": ...} or None

    def run(self):
        self.checked.emit(core.check_for_update())
