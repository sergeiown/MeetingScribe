"""Live audio recording: microphone (shared or WASAPI-exclusive) and
system-audio loopback, unified behind one Recorder interface - and both at
once, mixed down into a single output file.

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
is therefore only ever honored for a microphone source.

Recording both sources at once mixes them (simple summed gain, not
loudness-normalized) into one output file rather than two separate tracks -
this app's pipeline (and its transcript UI) is built around one audio file
per recording, and a meeting recording wants both sides of the
conversation in the same transcript anyway. The two sources are captured
by independently-clocked devices with no shared timeline, so they're
aligned by wall-clock draining on a fixed tick (see _mix_and_write) rather
than sample-accurately synchronized - adequate for speech transcription,
not for professional multi-track work."""

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

_CHUNK_SECONDS = 0.05  # loopback pull size, mix-tick interval, and a reasonable meter update grain
_MIX_GAIN = 0.7  # per source when mixing two, so simultaneous loud sources don't clip


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


class _SourceStream:
    """One live capture source (microphone or loopback), pushing raw
    float32 blocks into its own queue - mixing/writing happens outside,
    in Recorder, so this class knows nothing about the output file."""

    def __init__(self, device: AudioDevice, loopback: bool, exclusive: bool,
                 channels: int, samplerate: int, on_error):
        self.device = device
        self.loopback = loopback
        self.exclusive = exclusive and not loopback
        self.channels = channels
        self.samplerate = samplerate
        self._on_error = on_error
        self.blocks = queue.Queue()
        self._stream = None
        self._pull_thread = None
        self._stop_flag = threading.Event()
        self._stopped_cleanly = False

    def start(self) -> None:
        if self.loopback:
            self._start_loopback()
        else:
            self._start_microphone()

    def _start_microphone(self):
        import sounddevice as sd

        settings = sd.WasapiSettings(exclusive=True) if self.exclusive else None

        def callback(indata, frames, time_info, status):
            if status:
                _log.debug("Recording stream status: %s", status)
            self.blocks.put(indata.copy())

        try:
            self._stream = sd.InputStream(
                device=self.device.sd_index, channels=self.channels, samplerate=self.samplerate,
                dtype="float32", extra_settings=settings, callback=callback,
                finished_callback=self._on_finished)
            self._stream.start()
        except Exception as e:
            self._stream = None
            raise RecordingError(_classify_open_error(e)) from e

    def _start_loopback(self):
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
                    mic = sc.get_microphone(self.device.name, include_loopback=True)
                    with mic.recorder(samplerate=self.samplerate, channels=self.channels) as rec:
                        ready.set()
                        chunk = max(1, int(self.samplerate * _CHUNK_SECONDS))
                        while not self._stop_flag.is_set():
                            data = rec.record(numframes=chunk)
                            self.blocks.put(data)
                except Exception as e:
                    if not ready.is_set():
                        open_error.append(e)
                        ready.set()
                    elif not self._stopped_cleanly:
                        self._on_error(f"Recording device stopped unexpectedly: {e}")
            finally:
                ctypes.windll.ole32.CoUninitialize()

        self._pull_thread = threading.Thread(target=pull_loop, daemon=True)
        self._pull_thread.start()
        ready.wait(timeout=5.0)
        if open_error:
            self._pull_thread = None
            raise RecordingError(_classify_open_error(open_error[0]))

    def _on_finished(self):
        if not self._stopped_cleanly:
            self._on_error("Recording device stopped unexpectedly.")

    def drain(self) -> np.ndarray:
        """Whatever's queued right now, concatenated into one array (may be empty)."""
        chunks = []
        try:
            while True:
                chunks.append(self.blocks.get_nowait())
        except queue.Empty:
            pass
        if not chunks:
            return np.zeros((0, self.channels), dtype=np.float32)
        return np.concatenate(chunks, axis=0)

    def stop(self):
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


class Recorder:
    """One recording session: create, start, poll drain_events(), stop,
    discard. Not reusable across sessions."""

    def __init__(self, devices, out_path, *, exclusive: bool = False):
        """devices: [(AudioDevice, is_loopback), ...] - one entry for a
        single source, two for simultaneous mic+system (mixed down to one
        output file, see module docstring). exclusive only ever applies to
        a microphone entry."""
        self._device_specs = list(devices)
        self._out_path = Path(out_path)
        self._exclusive = exclusive
        self._sources = []
        self._events = queue.Queue()
        self._wav = None
        self._mix_thread = None
        self._stop_flag = threading.Event()
        self._start_time = None

    def start(self) -> None:
        channels = max(1, min(max(d.channels for d, _ in self._device_specs), 2))
        samplerate = int(self._device_specs[0][0].default_samplerate) or 48000

        try:
            self._wav = wave.open(str(self._out_path), "wb")
            self._wav.setnchannels(channels)
            self._wav.setsampwidth(2)
            self._wav.setframerate(samplerate)
        except OSError as e:
            raise RecordingError(f"Could not create the recording file: {e}") from e

        self._sources = [
            _SourceStream(device, loopback, self._exclusive, channels, samplerate,
                          on_error=lambda msg: self._events.put(("error", msg)))
            for device, loopback in self._device_specs
        ]
        started = []
        try:
            for src in self._sources:
                src.start()
                started.append(src)
        except RecordingError:
            for src in started:
                src.stop()
            self._sources = []
            self._close_wav()
            raise

        self._start_time = time.monotonic()
        self._stop_flag.clear()
        self._mix_thread = threading.Thread(target=self._mix_loop, daemon=True)
        self._mix_thread.start()

    def _mix_loop(self):
        while not self._stop_flag.is_set():
            time.sleep(_CHUNK_SECONDS)
            self._mix_and_write()
        self._mix_and_write()  # final drain of whatever arrived since the last tick

    def _mix_and_write(self):
        blocks = [src.drain() for src in self._sources]
        if len(blocks) == 1:
            mixed = blocks[0]
        else:
            n = min(b.shape[0] for b in blocks)
            if n == 0:
                mixed = np.zeros((0, blocks[0].shape[1]), dtype=np.float32)
            else:
                mixed = sum(b[:n] * _MIX_GAIN for b in blocks)
        if mixed.size == 0:
            return
        peak = float(np.abs(mixed).max())
        pcm16 = np.clip(mixed * 32767.0, -32768, 32767).astype(np.int16)
        if self._wav is not None:
            try:
                self._wav.writeframes(pcm16.tobytes())
            except Exception:
                pass  # already closed by stop()
        self._events.put(("level", peak))

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
        self._stop_flag.set()
        if self._mix_thread is not None:
            self._mix_thread.join(timeout=3.0)
            self._mix_thread = None
        for src in self._sources:
            src.stop()
        self._sources = []
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
        return self._mix_thread is not None
