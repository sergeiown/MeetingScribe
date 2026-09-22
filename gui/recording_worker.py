"""GUI-side controller for a live recording session.

Recorder only touches sounddevice/soundcard + wave, no ML libraries and no
matplotlib/pyannote - none of the Qt-conflict risk that justifies running
transcription/diarization in a separate OS process (see process_worker.py)
applies here. PortAudio's/soundcard's own callback/pull thread already runs
off the GUI thread (sounddevice spins it up internally; the loopback path
uses one plain threading.Thread - see core/recording.py), so this only
needs a QTimer draining a queue, not a QThread of its own."""

from PySide6.QtCore import QObject, QTimer, Signal

import core

_POLL_MS = 40  # faster than the 100ms ML-progress poll - a level meter needs to feel closer to real-time


class RecordingController(QObject):
    levels = Signal(list)   # [peak, ...] - one 0.0-1.0 peak per active source
    error = Signal(str)     # RecordingError message, or an unexpected mid-recording stop
    stopped = Signal(str)   # final file path, once stop() completes

    def __init__(self, devices, out_path, *, exclusive: bool, parent=None):
        """devices: [(core.AudioDevice, is_loopback), ...] - one entry for a
        single source, two for simultaneous mic+system."""
        super().__init__(parent)
        self._recorder = core.Recorder(devices, out_path, exclusive=exclusive)
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._drain)

    def start(self) -> None:
        """Raises core.RecordingError synchronously - the caller shows it inline."""
        self._recorder.start()
        self._poll_timer.start(_POLL_MS)

    def stop(self) -> None:
        self._poll_timer.stop()
        path = self._recorder.stop()
        self.stopped.emit(str(path))

    def pause(self) -> None:
        self._recorder.pause()

    def resume(self) -> None:
        self._recorder.resume()

    @property
    def is_paused(self) -> bool:
        return self._recorder.is_paused

    def elapsed_seconds(self) -> float:
        return self._recorder.elapsed_seconds()

    def _drain(self):
        for kind, payload in self._recorder.drain_events():
            if kind == "levels":
                self.levels.emit(payload)
            elif kind == "error":
                self.error.emit(payload)
