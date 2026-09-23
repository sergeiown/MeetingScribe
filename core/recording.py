"""Live audio recording: microphone (shared or WASAPI-exclusive) and
system-audio loopback, unified behind one Recorder interface - and both at
once, mixed down into a single output file.

Two backends, chosen per source, both confirmed by direct testing on real
hardware:
- Microphone: sounddevice, i.e. WASAPI directly (shared by default,
  optionally sd.WasapiSettings(exclusive=True)). An ffmpeg/dshow-based
  microphone engine was tried in between and reverted: it looked
  attractive (mature, external capture pipeline, matched the user's own
  side-by-side OBS comparison) but direct testing with a real Bluetooth
  headset used as *both* the microphone and the system-audio loopback
  target at once showed the dshow session degrading the microphone
  signal substantially, while a plain WASAPI session alongside the same
  loopback showed no such degradation and zero PortAudio-reported
  overflow/error status - DirectShow's audio capture (superseded by
  WASAPI for audio since Vista) evidently negotiates a Bluetooth
  Hands-Free-Profile connection worse than a native WASAPI session does,
  which is also almost certainly why OBS (WASAPI-based, not DirectShow)
  never showed this problem on the same hardware in the user's own test.
  WASAPI is therefore the one microphone backend, in both modes.
- System-audio loopback: soundcard, via sc.get_microphone(name,
  include_loopback=True). sounddevice's installed build has no loopback
  support at all (no such parameter exists on its WasapiSettings), and
  ffmpeg's bundled build has no WASAPI-loopback demuxer either (confirmed:
  `ffmpeg -devices` lists dshow/gdigrab/openal/vfwcap/lavfi/libcdio, no
  wasapi) - dshow's usual loopback route ("Stereo Mix") is a legacy
  device that's absent or disabled on most modern hardware.

Exclusive mode is only ever honored for a microphone entry - loopback
capture of system/output audio is architecturally always shared in
Windows (OBS, Discord, this app, ... can all tap the same render stream
simultaneously by design), so there's no stronger "exclusive" variant to
request for it.

On some real hardware, a WASAPI *exclusive-mode* stream's actual delivered
throughput does not match the rate it was opened with/reports (confirmed
by direct testing: one real microphone's exclusive-mode stream, opened at
a requested/reported 48000 Hz, actually delivered audio at ~66000-71000 Hz,
varying between separate recordings by a few percent). Shared mode did
not show this (its small ~2% timing error matched plain measurement
noise). Rather than branch behavior on the mode, every source is measured
and corrected the same way: PortAudio's own per-callback device-clock
timestamp (time_info.currentTime) gives each microphone's true rate
without any Python-side wall-clock jitter (thread wake-up latency, GIL
scheduling) - confirmed far tighter than measuring wall-clock time around
the recording (0.0-0.2% error vs several percent). Loopback has no
equivalent timestamp, so it falls back to wall-clock timing. Each
source's raw captured audio is written untouched to its own temporary
file during the session and resampled exactly once at stop(), with
scipy.signal.resample_poly - a proper polyphase FIR resampler with
anti-aliasing, meant for real, non-periodic audio like speech (unlike the
FFT-based resample(), which assumes one periodic cycle and can ring/
distort on real recordings) - onto a fixed target rate. Only then are the
(now equally and correctly timed) sources mixed and written to the final
WAV."""

import logging
import queue
import tempfile
import threading
import time
import wave
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Optional

import numpy as np
from scipy.signal import resample_poly

_log = logging.getLogger(__name__)

_CHUNK_SECONDS = 0.05  # mix-tick interval and meter update grain
_MIX_GAIN = 0.7  # per source when mixing two, so simultaneous loud sources don't clip
_TARGET_RATE = 48000  # every recording ends up at this rate, regardless of what the device actually did
_DEVICE_OPEN_RETRIES = 3  # see _start_with_retry - rides out transient device-busy failures
_DEVICE_OPEN_RETRY_DELAY = 0.4


def _start_with_retry(start_fn) -> None:
    """Retries a device-open callable a few times before giving up - a
    real recording started with both the microphone and system audio
    pointed at the same Bluetooth headset failed intermittently with
    "already in use", confirmed to be a transient race: Windows switches
    that headset from output-only to a bidirectional Bluetooth profile the
    moment the microphone side opens, and briefly reports the device busy
    to whichever side tries to open during that switch. A plain, bounded
    retry is the standard way to ride out a transient device-open failure
    like this, rather than trying to detect or wait for the profile switch
    directly."""
    last_error = None
    for attempt in range(_DEVICE_OPEN_RETRIES):
        try:
            start_fn()
            return
        except RecordingError as e:
            last_error = e
            if attempt < _DEVICE_OPEN_RETRIES - 1:
                time.sleep(_DEVICE_OPEN_RETRY_DELAY)
    raise last_error


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
    # PortAudio builds its device table once, at initialization, and does
    # not notice devices plugged/unplugged afterward (confirmed: a
    # Bluetooth headset connected after the process started was invisible
    # here until this) - forcing a re-init makes it re-enumerate, which is
    # the only way a long-running app process picks up e.g. a Bluetooth
    # headset connected (or made the Windows default) after it started.
    sd._terminate()
    sd._initialize()
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
        # Microphone only: PortAudio's own per-callback device-clock
        # timestamp, spanning first callback to most recent - see module
        # docstring for why this replaces Python-side wall-clock timing
        # for the rate measurement wherever it's available. Loopback
        # (soundcard) exposes no equivalent timestamp, so it has none.
        self._first_device_time = None
        self._last_device_time = None

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
            if self._first_device_time is None:
                self._first_device_time = time_info.currentTime
            self._last_device_time = time_info.currentTime
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

    def device_duration(self) -> Optional[float]:
        """Real elapsed time spanned by this source's own callbacks,
        measured on PortAudio's device clock - None if unavailable
        (loopback, or a mic source that never received a callback)."""
        if self._first_device_time is None or self._last_device_time is None:
            return None
        return max(0.0, self._last_device_time - self._first_device_time)

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
    (measuring each source's true rate, resampling, mixing) happens once,
    in stop() - see module docstring for why."""

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
        self._source_channels = []
        self._raw_paths = []
        self._raw_files = []
        self._raw_frame_counts = []
        self._workdir = None

    def start(self) -> None:
        # The final mixed output uses the widest channel count among the
        # sources, but each source is opened at its *own* native channel
        # count - not this shared one. Forcing every source to the same
        # count broke a real, deterministic case: a mono-only Bluetooth
        # microphone paired with a stereo loopback source failed to open
        # at all ("Invalid number of channels"), since the mic doesn't
        # support the 2 channels the (unrelated) loopback source has.
        # Mismatched channel counts are reconciled once, after resampling,
        # in _load_and_resample.
        self._source_channels = [max(1, min(d.channels, 2)) for d, _ in self._device_specs]
        self._channels = max(self._source_channels)
        self._workdir = Path(tempfile.mkdtemp(prefix="meetingscribe_rec_"))

        self._sources = [
            _SourceStream(device, loopback, self._exclusive, src_channels,
                          int(device.default_samplerate) or _TARGET_RATE,
                          on_error=lambda msg: self._events.put(("error", msg)))
            for (device, loopback), src_channels in zip(self._device_specs, self._source_channels)
        ]
        started = []
        try:
            for src in self._sources:
                _start_with_retry(src.start)
                started.append(src)
        except RecordingError:
            for src in started:
                src.stop()
            self._sources = []
            raise

        try:
            self._raw_paths = [
                Path(tempfile.mkstemp(prefix="meetingscribe_rec_", suffix=f".src{i}.raw",
                                      dir=str(self._workdir))[1])
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

        # Device-clock span, when available (microphone), bypasses
        # Python-side wall-clock jitter for the rate measurement - see
        # _SourceStream.device_duration and module docstring. Captured
        # before src.stop() (harmless either way, but keeps the snapshot
        # unambiguous).
        wall_clock_duration = self.elapsed_seconds()
        device_durations = [src.device_duration() for src in self._sources]

        for src in self._sources:
            src.stop()
        self._sources = []

        for f in self._raw_files:
            try:
                f.close()
            except Exception:
                pass
        self._raw_files = []

        resampled = []
        for path, frames, device_duration, src_channels in zip(
                self._raw_paths, self._raw_frame_counts, device_durations, self._source_channels):
            active_duration = (max(0.0, device_duration - self._paused_accum)
                                if device_duration is not None else wall_clock_duration)
            resampled.append(self._load_and_resample(path, frames, active_duration, src_channels))
        self._raw_paths = []

        mixed = resampled[0] if len(resampled) == 1 else self._mix_final(resampled)
        self._write_wav(mixed)
        if self._workdir is not None:
            import shutil
            shutil.rmtree(self._workdir, ignore_errors=True)
            self._workdir = None
        return self._out_path

    def _load_and_resample(self, path: Path, frames_written: int, active_duration: float,
                            src_channels: int) -> np.ndarray:
        if frames_written == 0 or active_duration <= 0:
            return np.zeros((0, self._channels), dtype=np.float32)
        raw = np.fromfile(path, dtype=np.float32).reshape(-1, src_channels)
        true_rate = frames_written / active_duration
        if 8000 <= true_rate <= 192000:
            # resample_poly (polyphase FIR, proper anti-aliasing), not the
            # FFT-based resample(): the latter treats the signal as one
            # periodic cycle, which is wrong for real, non-periodic speech
            # and shows up as ringing/artifacts - resample_poly is the
            # function actually meant for real-world audio like this.
            ratio = Fraction(_TARGET_RATE / true_rate).limit_denominator(10_000)
            raw = resample_poly(raw, ratio.numerator, ratio.denominator, axis=0).astype(np.float32)
        # implausible rate measurement: use as captured rather than distort it further
        if src_channels == self._channels:
            return raw
        if src_channels == 1:
            return np.repeat(raw, self._channels, axis=1)  # mono mic alongside a stereo source - duplicate, don't drop
        return raw.mean(axis=1, keepdims=True).repeat(self._channels, axis=1)

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
