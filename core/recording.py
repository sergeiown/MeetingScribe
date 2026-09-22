"""Live audio recording: microphone (shared or WASAPI-exclusive) and
system-audio loopback, unified behind one Recorder interface.

Two backends, chosen per source, both confirmed by direct testing on real
hardware (neither the bundled ffmpeg build nor QtMultimedia's QAudioSource
can do either on Windows):
- Microphone: sounddevice, via sounddevice.WasapiSettings(exclusive=True).
  soundcard's own exclusive_mode was tried first and reliably raises
  "invalid argument" on real hardware regardless of samplerate/channels;
  sounddevice's exclusive mode works cleanly.
- System-audio loopback: soundcard, via sc.get_microphone(name,
  include_loopback=True). sounddevice's installed build has no loopback
  support at all (no such parameter exists on its WasapiSettings).

WASAPI exclusive mode is only a meaningful concept for a real capture
device (the microphone): it means no other app can open that same device
while we hold it, and our own open attempt fails cleanly if another app
already holds it exclusively. Loopback capture of system/output audio is
architecturally always shared in Windows - multiple apps (this one, OBS,
Discord, ...) can all tap the same render stream simultaneously by design,
so there is no stronger "exclusive" variant to request for it. `exclusive`
is therefore only ever honored for microphone recording.
"""

import logging
import queue
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

_log = logging.getLogger(__name__)

_CHUNK_SECONDS = 0.05  # loopback pull size; also a reasonable meter update grain


class RecordingError(Exception):
    """Recording could not start, or stopped unexpectedly (device busy,
    exclusive mode denied, device disconnected mid-recording)."""


@dataclass
class AudioDevice:
    name: str
    is_default: bool
    channels: int
    default_samplerate: float
    sd_index: Optional[int] = None  # sounddevice device index; microphones only


def _classify_open_error(e: Exception) -> str:
    low = str(e).lower()
    if "invalid number of channels" in low or "invalid sample rate" in low or "invalid argument" in low:
        return "This device is already in use by another app, or refused exclusive access."
    if "device unavailable" in low or "unanticipated host error" in low:
        return "This device is already in use by another app."
    return f"Could not open this device: {e}"


def list_microphones() -> list:
    """WASAPI-capable input devices - the only ones exclusive mode applies to."""
    import sounddevice as sd
    hostapis = sd.query_hostapis()
    wasapi_idx = next((i for i, a in enumerate(hostapis) if "WASAPI" in a["name"]), None)
    if wasapi_idx is None:
        return []
    default_in = hostapis[wasapi_idx]["default_input_device"]
    devices = []
    for i, d in enumerate(sd.query_devices()):
        if d["hostapi"] == wasapi_idx and d["max_input_channels"] > 0:
            devices.append(AudioDevice(
                name=d["name"], is_default=(i == default_in),
                channels=min(d["max_input_channels"], 2),
                default_samplerate=d["default_samplerate"] or 48000.0,
                sd_index=i))
    return devices


def list_loopback_outputs() -> list:
    """Output (speaker) devices that can be loopback-captured for
    system-audio recording - always shared mode, see module docstring."""
    import soundcard as sc
    try:
        default_name = sc.default_speaker().name
    except Exception:
        default_name = None
    devices = []
    for spk in sc.all_speakers():
        devices.append(AudioDevice(
            name=spk.name, is_default=(spk.name == default_name),
            channels=spk.channels or 2, default_samplerate=48000.0, sd_index=None))
    return devices


class Recorder:
    """One recording session: create, start, poll drain_events(), stop,
    discard. Not reusable across sessions."""

    def __init__(self, device: AudioDevice, out_path, *,
                 loopback: bool = False, exclusive: bool = False):
        self._device = device
        self._out_path = Path(out_path)
        self._loopback = loopback
        self._exclusive = exclusive and not loopback
        self._events = queue.Queue()
        self._wav = None
        self._stream = None       # sounddevice stream (microphone path)
        self._pull_thread = None  # soundcard pull thread (loopback path)
        self._stop_flag = threading.Event()
        self._start_time = None
        self._stopped_cleanly = False

    def start(self) -> None:
        channels = max(1, min(self._device.channels, 2))
        samplerate = int(self._device.default_samplerate) or 48000
        try:
            self._wav = wave.open(str(self._out_path), "wb")
            self._wav.setnchannels(channels)
            self._wav.setsampwidth(2)
            self._wav.setframerate(samplerate)
        except OSError as e:
            raise RecordingError(f"Could not create the recording file: {e}") from e

        self._start_time = time.monotonic()
        try:
            if self._loopback:
                self._start_loopback(channels, samplerate)
            else:
                self._start_microphone(channels, samplerate)
        except RecordingError:
            self._close_wav()
            raise

    def _start_microphone(self, channels, samplerate):
        import sounddevice as sd

        settings = sd.WasapiSettings(exclusive=True) if self._exclusive else None

        def callback(indata, frames, time_info, status):
            if status:
                _log.debug("Recording stream status: %s", status)
            self._write_and_meter(indata)

        try:
            self._stream = sd.InputStream(
                device=self._device.sd_index, channels=channels, samplerate=samplerate,
                dtype="float32", extra_settings=settings, callback=callback,
                finished_callback=self._on_stream_finished)
            self._stream.start()
        except Exception as e:
            self._stream = None
            raise RecordingError(_classify_open_error(e)) from e

    def _start_loopback(self, channels, samplerate):
        self._stop_flag.clear()
        ready = threading.Event()
        open_error = []

        def pull_loop():
            import ctypes
            # soundcard's WASAPI calls are COM calls, and COM must be
            # initialized per-thread - it only gets initialized (once, as a
            # side effect) on whichever thread first imports
            # soundcard.mediafoundation, which is never this pull thread.
            # Without this, mic.recorder() fails with CO_E_NOTINITIALIZED
            # (0x800401f0) - confirmed by direct testing.
            ctypes.windll.ole32.CoInitializeEx(None, 0)
            try:
                import soundcard as sc
                try:
                    mic = sc.get_microphone(self._device.name, include_loopback=True)
                    with mic.recorder(samplerate=samplerate, channels=channels) as rec:
                        ready.set()
                        chunk = max(1, int(samplerate * _CHUNK_SECONDS))
                        while not self._stop_flag.is_set():
                            data = rec.record(numframes=chunk)
                            self._write_and_meter(data)
                except Exception as e:
                    if not ready.is_set():
                        open_error.append(e)
                        ready.set()
                    elif not self._stopped_cleanly:
                        self._events.put(("error", f"Recording device stopped unexpectedly: {e}"))
            finally:
                ctypes.windll.ole32.CoUninitialize()

        self._pull_thread = threading.Thread(target=pull_loop, daemon=True)
        self._pull_thread.start()
        ready.wait(timeout=5.0)
        if open_error:
            self._pull_thread = None
            raise RecordingError(_classify_open_error(open_error[0]))

    def _write_and_meter(self, block: np.ndarray) -> None:
        peak = float(np.abs(block).max()) if block.size else 0.0
        pcm16 = np.clip(block * 32767.0, -32768, 32767).astype(np.int16)
        if self._wav is not None:
            try:
                self._wav.writeframes(pcm16.tobytes())
            except Exception:
                pass  # already closed by stop()
        self._events.put(("level", peak))

    def _on_stream_finished(self):
        if not self._stopped_cleanly:
            self._events.put(("error", "Recording device stopped unexpectedly."))

    def drain_events(self) -> list:
        """[("level", float) | ("error", str), ...] accumulated since the last call."""
        events = []
        try:
            while True:
                events.append(self._events.get_nowait())
        except queue.Empty:
            pass
        return events

    def elapsed_seconds(self) -> float:
        return time.monotonic() - self._start_time if self._start_time else 0.0

    def stop(self) -> Path:
        self._stopped_cleanly = True
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._pull_thread is not None:
            self._stop_flag.set()
            self._pull_thread.join(timeout=2.0)
            self._pull_thread = None
        self._close_wav()
        return self._out_path

    def _close_wav(self):
        if self._wav is not None:
            try:
                self._wav.close()
            except Exception:
                pass
            self._wav = None

    @property
    def is_recording(self) -> bool:
        return self._stream is not None or self._pull_thread is not None
