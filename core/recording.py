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

On some real hardware, a WASAPI stream's actual delivered throughput does
not reliably match the rate it was opened with/reports - confirmed by
direct testing (one real microphone's exclusive-mode stream, opened at a
requested/reported 48000 Hz, actually delivered audio at ~66000-71000 Hz,
varying between separate recordings by a few percent even with nothing
else changed). A short calibration measurement (tried first, several
window sizes) could not pin this down reliably enough: repeating the same
short measurement back to back kept landing at different values, and
whichever one a given recording happened to get became that whole file's
label - occasionally sped up or slowed down enough to be clearly audible.

The fix used here mirrors how established recording software (confirmed
directly: OBS via its own WASAPI capture, same hardware, same microphone)
avoids this problem - it never trusts a short sample of device timing, and
it never hand-rolls a resampler. Every source's raw captured audio is
written untouched to its own temporary file during the session; at
stop(), each source's *true* rate is computed from the one measurement
that actually is reliable - total frames captured divided by the
recording's total real (wall-clock, pause-excluded) duration, averaged
over the entire session rather than a short window - and then the whole
per-source signal is resampled exactly once, with scipy.signal.resample
(a real, tested DSP routine, not a hand-written interpolation loop), onto
a fixed target rate. Only then are the (now equally and correctly timed)
sources mixed and written to the final WAV. This is deliberately the
simplest structure that is still correct: one clean measurement per
session, one library resampling call per source, no per-tick math to get
subtly wrong."""

import logging
import queue
import tempfile
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from scipy.signal import resample

_log = logging.getLogger(__name__)

_CHUNK_SECONDS = 0.05  # live meter update grain only - no longer tied to any resampling
_MIX_GAIN = 0.7  # per source when mixing two, so simultaneous loud sources don't clip
_TARGET_RATE = 48000  # every recording is resampled to this, regardless of what the device actually did


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


def resolve_device(devices: list, preferred_name):
    """Picks preferred_name from devices if it's still present, else the
    current OS-default device, else the first available (or None if the
    list is empty). preferred_name=None means "use the system default",
    resolved fresh against the live device list rather than a pinned name."""
    if preferred_name:
        match = next((d for d in devices if d.name == preferred_name), None)
        if match is not None:
            return match
    return next((d for d in devices if d.is_default), devices[0] if devices else None)


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
    discard. Not reusable across sessions.

    Nothing is mixed or rate-corrected while recording - each source's raw
    captured audio streams straight to its own temporary file (bounded
    memory regardless of recording length, same crash-safety profile as
    writing the final file incrementally would have). All the real work
    (measuring each source's true rate over the whole session, resampling,
    mixing) happens once, in stop() - see module docstring for why."""

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
        self._mix_thread = None
        self._stop_flag = threading.Event()
        self._start_time = None
        self._paused_since = None
        self._paused_accum = 0.0
        self._channels = 1
        self._raw_paths = []
        self._raw_files = []
        self._raw_frame_counts = []

    def start(self) -> None:
        channels = max(1, min(max(d.channels for d, _ in self._device_specs), 2))
        self._channels = channels
        requested_samplerate = int(self._device_specs[0][0].default_samplerate) or 48000

        self._sources = [
            _SourceStream(device, loopback, self._exclusive, channels, requested_samplerate,
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
            raise

        try:
            self._raw_paths = [
                Path(tempfile.mkstemp(prefix="meetingscribe_rec_", suffix=f".src{i}.raw")[1])
                for i in range(len(self._sources))
            ]
            self._raw_files = [open(p, "wb") for p in self._raw_paths]
        except OSError as e:
            for src in self._sources:
                src.stop()
            self._sources = []
            raise RecordingError(f"Could not create a temporary recording file: {e}") from e
        self._raw_frame_counts = [0] * len(self._sources)

        self._start_time = time.monotonic()
        self._stop_flag.clear()
        self._mix_thread = threading.Thread(target=self._mix_loop, daemon=True)
        self._mix_thread.start()

    def _mix_loop(self):
        while not self._stop_flag.is_set():
            time.sleep(_CHUNK_SECONDS)
            self._drain_and_meter()
        self._drain_and_meter()  # final drain since the last tick

    def pause(self) -> None:
        """Keeps the underlying streams open (avoids re-opening an exclusive
        device) - just stops writing audio to the file and freezes the
        elapsed-time counter."""
        if self._paused_since is None:
            self._paused_since = time.monotonic()

    def resume(self) -> None:
        if self._paused_since is not None:
            self._paused_accum += time.monotonic() - self._paused_since
            self._paused_since = None

    @property
    def is_paused(self) -> bool:
        return self._paused_since is not None

    def _drain_and_meter(self):
        blocks = [src.drain() for src in self._sources]
        per_source_peaks = [float(np.abs(b).max()) if b.size else 0.0 for b in blocks]
        if self._paused_since is None:
            for i, block in enumerate(blocks):
                if block.size:
                    try:
                        self._raw_files[i].write(block.astype(np.float32, copy=False).tobytes())
                        self._raw_frame_counts[i] += block.shape[0]
                    except Exception:
                        pass  # already closed by stop()
        self._events.put(("levels", per_source_peaks))

    def drain_events(self) -> list:
        """[("levels", [peak, ...]) | ("error", str), ...] accumulated since the last call."""
        events = []
        try:
            while True:
                events.append(self._events.get_nowait())
        except queue.Empty:
            pass
        return events

    def elapsed_seconds(self) -> float:
        if not self._start_time:
            return 0.0
        now = time.monotonic()
        paused = self._paused_accum + (now - self._paused_since if self._paused_since is not None else 0.0)
        return max(0.0, now - self._start_time - paused)

    def stop(self) -> Path:
        self._stop_flag.set()
        if self._mix_thread is not None:
            self._mix_thread.join(timeout=3.0)
            self._mix_thread = None
        for src in self._sources:
            src.stop()
        self._sources = []

        # The one number that's actually reliable: total frames captured
        # over the *entire* active (pause-excluded) session, not a short
        # sample of it - see module docstring.
        active_duration = self.elapsed_seconds()
        for f in self._raw_files:
            try:
                f.close()
            except Exception:
                pass
        self._raw_files = []

        resampled = []
        for path, frames in zip(self._raw_paths, self._raw_frame_counts):
            resampled.append(self._load_and_resample(path, frames, active_duration))
            try:
                path.unlink()
            except OSError:
                pass
        self._raw_paths = []

        mixed = resampled[0] if len(resampled) == 1 else self._mix_final(resampled)
        self._write_wav(mixed)
        return self._out_path

    def _load_and_resample(self, path: Path, frames_written: int, active_duration: float) -> np.ndarray:
        if frames_written == 0 or active_duration <= 0:
            return np.zeros((0, self._channels), dtype=np.float32)
        raw = np.fromfile(path, dtype=np.float32).reshape(-1, self._channels)
        true_rate = frames_written / active_duration
        if not (8000 <= true_rate <= 192000):
            return raw  # implausible measurement - use as captured rather than distort it further
        target_frames = max(1, round(raw.shape[0] * _TARGET_RATE / true_rate))
        return resample(raw, target_frames, axis=0).astype(np.float32)

    def _mix_final(self, sources: list) -> np.ndarray:
        n = min(s.shape[0] for s in sources)
        if n == 0:
            return np.zeros((0, self._channels), dtype=np.float32)
        return sum(s[:n] * _MIX_GAIN for s in sources)

    def _write_wav(self, mixed: np.ndarray) -> None:
        wf = wave.open(str(self._out_path), "wb")
        try:
            wf.setnchannels(self._channels)
            wf.setsampwidth(2)
            wf.setframerate(_TARGET_RATE)
            if mixed.size:
                pcm16 = np.clip(mixed * 32767.0, -32768, 32767).astype(np.int16)
                wf.writeframes(pcm16.tobytes())
        finally:
            wf.close()

    @property
    def is_recording(self) -> bool:
        return self._mix_thread is not None
