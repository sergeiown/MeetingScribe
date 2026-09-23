"""Live audio recording: microphone and system-audio loopback, unified
behind one Recorder interface - and both at once, mixed down into a
single output file.

Three backends now, chosen per source and mode:
- Microphone, shared mode (the default): ffmpeg itself, via its dshow
  input (confirmed by direct testing to see and open this app's actual
  microphones by the same name sounddevice reports). This is a deliberate
  change from an earlier all-Python design that kept trying to correct
  WASAPI's own timing quirks itself (calibration windows, then per-tick
  resampling, then whole-session resampling with a real DSP library) and
  kept surfacing a new artifact each time. The user's own side-by-side
  test settled it: OBS, recording the same microphone, had none of these
  problems - and OBS does not hand-roll WASAPI capture either, it goes
  through a mature capture pipeline (in ffmpeg's case, dshow + its own
  libswresample) that already solves this correctly. Recorder's job for
  this path is now just lifecycle management (start/pause/resume/stop of
  an ffmpeg subprocess) and live level metering (via a second, parallel,
  shared-mode sounddevice stream used only to peek at levels - WASAPI
  shared mode allows multiple simultaneous opens by design, so this never
  competes with ffmpeg's own capture) - not audio math.
- Microphone, exclusive mode: kept as a secondary, legacy path via
  sounddevice's WasapiSettings(exclusive=True) - soundcard's own
  exclusive_mode reliably raises "invalid argument" on real hardware, so
  sounddevice is the only one of the two that supports it at all. ffmpeg's
  dshow has no equivalent to WASAPI exclusive access, so this mode simply
  is not available through it. Real hardware has shown this path's actual
  throughput can differ substantially from what it reports (confirmed:
  ~66-71 kHz on a stream opened at 48000 Hz) - see _load_and_resample for
  the correction still applied here. Because of that unreliability, this
  is presented in the UI as a secondary option, not the default.
- System-audio loopback: soundcard, via sc.get_microphone(name,
  include_loopback=True) - unchanged. sounddevice's installed build has no
  loopback support at all, and ffmpeg's bundled build has no WASAPI-loopback
  demuxer either (confirmed: `ffmpeg -devices` lists dshow/gdigrab/openal/
  vfwcap/lavfi/libcdio, no wasapi) - dshow's usual loopback route ("Stereo
  Mix") is a legacy device that's absent or disabled on most modern
  hardware, so it isn't a real alternative either.

Exclusive mode is only ever honored for a microphone entry - loopback
capture of system/output audio is architecturally always shared in
Windows (OBS, Discord, this app, ... can all tap the same render stream
simultaneously by design), so there's no stronger "exclusive" variant to
request for it."""

import logging
import queue
import shutil
import subprocess
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

from .audio import FFMPEG

_log = logging.getLogger(__name__)

_CHUNK_SECONDS = 0.05  # live meter update grain, and the legacy-path mix tick
_MIX_GAIN = 0.7  # per source when mixing two, so simultaneous loud sources don't clip
_TARGET_RATE = 48000  # every recording ends up at this rate, regardless of what the device actually did
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_FFMPEG_STOP_TIMEOUT = 5.0  # graceful "q" stop before escalating to terminate()
_FFMPEG_STARTUP_GRACE = 0.5  # after spawning, before trusting ffmpeg opened the device cleanly


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


def _classify_ffmpeg_error(stderr_text: str) -> str:
    low = stderr_text.lower()
    if "could not find audio" in low or "could not enumerate" in low or "no such" in low:
        return "This device could not be found - it may have been disconnected."
    if "error opening input" in low or "i/o error" in low or "immediate exit" in low:
        return "This device is already in use by another app, or could not be opened."
    tail = stderr_text.strip().splitlines()[-1:] if stderr_text.strip() else []
    return "Could not open the microphone." + (f" ({tail[0][:200]})" if tail else "")


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


def _load_wav_as_array(path: Path, channels: int) -> np.ndarray:
    if not path.exists() or path.stat().st_size == 0:
        return np.zeros((0, channels), dtype=np.float32)
    with wave.open(str(path), "rb") as wf:
        n = wf.getnframes()
        raw = wf.readframes(n)
    if not raw:
        return np.zeros((0, channels), dtype=np.float32)
    return np.frombuffer(raw, dtype=np.int16).reshape(-1, channels).astype(np.float32) / 32768.0


def _concat_wav_segments(segments: list, out_path: Path, channels: int) -> None:
    """Losslessly joins WAV segments (all already the same format - ffmpeg
    wrote every one of them itself) via ffmpeg's own concat demuxer - the
    plain, standard way to join a pause/resume session's pieces without
    re-encoding. A single segment is just copied; no segments (recording
    stopped before ffmpeg ever produced one) leaves an empty placeholder."""
    existing = [p for p in segments if p.exists() and p.stat().st_size > 0]
    if not existing:
        wf = wave.open(str(out_path), "wb")
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(_TARGET_RATE)
        wf.close()
        return
    if len(existing) == 1:
        shutil.copyfile(existing[0], out_path)
        return
    list_path = existing[0].parent / f"{out_path.stem}_concat_list.txt"
    with open(list_path, "w", encoding="utf-8") as f:
        for p in existing:
            f.write(f"file '{p.name}'\n")
    subprocess.run(
        [FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", str(out_path)],
        cwd=str(existing[0].parent), capture_output=True, creationflags=_CREATE_NO_WINDOW)
    try:
        list_path.unlink()
    except OSError:
        pass


class _FfmpegMicCapture:
    """Segmented microphone capture via ffmpeg's own dshow input - the
    default/primary microphone engine (see module docstring). ffmpeg owns
    capture, resampling (via -ar/-ac), and file writing end-to-end using
    its own mature code; this class only manages *when* it's recording.
    Pause/resume works by ending the current segment and starting a new
    one (ffmpeg has no live pause signal for a running capture) - stop()
    hands back every segment path for the caller to concatenate."""

    def __init__(self, device_name: str, channels: int, workdir: Path):
        self.device_name = device_name
        self.channels = channels
        self._workdir = workdir
        self._segments = []
        self._proc = None
        self._stderr_lines = []

    def _spawn_segment(self) -> None:
        seg_path = self._workdir / f"mic_seg_{len(self._segments)}.wav"
        args = [FFMPEG, "-y", "-f", "dshow", "-i", f"audio={self.device_name}",
                "-ar", str(_TARGET_RATE), "-ac", str(self.channels),
                "-acodec", "pcm_s16le", str(seg_path)]
        self._stderr_lines = []
        self._proc = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, creationflags=_CREATE_NO_WINDOW)

        def read_stderr(proc=self._proc):
            try:
                for line in proc.stderr:
                    self._stderr_lines.append(line.decode("utf-8", "replace"))
            except Exception:
                pass

        threading.Thread(target=read_stderr, daemon=True).start()
        self._segments.append(seg_path)

    def start(self) -> None:
        if not FFMPEG:
            raise RecordingError("ffmpeg was not found - it's required for microphone recording.")
        self._spawn_segment()
        time.sleep(_FFMPEG_STARTUP_GRACE)
        if self._proc.poll() is not None:
            raise RecordingError(_classify_ffmpeg_error("".join(self._stderr_lines)))

    def _stop_current_segment(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.write(b"q")
                proc.stdin.flush()
        except Exception:
            pass
        try:
            proc.wait(timeout=_FFMPEG_STOP_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()

    def pause(self) -> None:
        self._stop_current_segment()

    def resume(self) -> None:
        self._spawn_segment()

    def finish(self) -> list:
        """Stops the current segment (if any) and returns every segment path."""
        self._stop_current_segment()
        return self._segments


class _MeterOnlyMicStream:
    """A second, parallel, shared-mode microphone stream used only to
    drive the live level meter while ffmpeg owns the actual recording -
    WASAPI shared mode allows multiple simultaneous opens by design, so
    this never competes with ffmpeg's own capture. Best-effort: metering
    is a nice-to-have, never allowed to fail or block the real recording."""

    def __init__(self, sd_index: Optional[int], channels: int, samplerate: int):
        self.sd_index = sd_index
        self.channels = channels
        self.samplerate = samplerate
        self._stream = None
        self._peak = 0.0
        self._lock = threading.Lock()

    def start(self) -> None:
        import sounddevice as sd

        def callback(indata, frames, time_info, status):
            peak = float(np.abs(indata).max()) if indata.size else 0.0
            with self._lock:
                self._peak = max(self._peak, peak)

        try:
            self._stream = sd.InputStream(
                device=self.sd_index, channels=self.channels, samplerate=self.samplerate,
                dtype="float32", callback=callback)
            self._stream.start()
        except Exception:
            self._stream = None

    def take_peak(self) -> float:
        with self._lock:
            peak, self._peak = self._peak, 0.0
        return peak

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None


class _SourceStream:
    """One live capture source for the legacy path (exclusive-mode
    microphone, or loopback), pushing raw float32 blocks into its own
    queue - mixing/writing happens outside, in Recorder."""

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
        # timestamp, spanning first callback to most recent - removes
        # Python-side wall-clock jitter from the rate measurement. Loopback
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

    Each device_spec gets its own engine, chosen per module docstring:
    a non-exclusive microphone gets the ffmpeg-owned path (_FfmpegMicCapture
    + a meter-only stream); an exclusive microphone or a loopback source
    gets the legacy sounddevice/soundcard path (_SourceStream, writing raw
    audio to its own temp file, resampled once at stop() - see
    _load_and_resample). Every engine's final signal ends up as one
    float32 array at _TARGET_RATE, in the same order as device_specs, so
    stop()'s mixing step and drain_events()'s per-source level order don't
    need to know which engine produced which array."""

    def __init__(self, devices, out_path, *, exclusive: bool = False):
        """devices: [(AudioDevice, is_loopback), ...] - one entry for a
        single source, two for simultaneous mic+system (mixed down to one
        output file, see module docstring). exclusive only ever applies to
        a microphone entry."""
        self._device_specs = list(devices)
        self._out_path = Path(out_path)
        self._exclusive = exclusive
        self._events = queue.Queue()
        self._mix_thread = None
        self._stop_flag = threading.Event()
        self._start_time = None
        self._paused_since = None
        self._paused_accum = 0.0
        self._channels = 1
        self._engines = []
        self._workdir = None

    def start(self) -> None:
        channels = max(1, min(max(d.channels for d, _ in self._device_specs), 2))
        self._channels = channels
        self._workdir = Path(tempfile.mkdtemp(prefix="meetingscribe_rec_"))

        engines = []
        try:
            for device, loopback in self._device_specs:
                if not loopback and not self._exclusive:
                    capture = _FfmpegMicCapture(device.name, channels, self._workdir)
                    capture.start()
                    meter = _MeterOnlyMicStream(device.sd_index, channels,
                                                 int(device.default_samplerate) or _TARGET_RATE)
                    meter.start()
                    engines.append({"kind": "ffmpeg_mic", "capture": capture, "meter": meter})
                else:
                    requested_samplerate = int(device.default_samplerate) or _TARGET_RATE
                    src = _SourceStream(device, loopback, self._exclusive, channels, requested_samplerate,
                                        on_error=lambda msg: self._events.put(("error", msg)))
                    src.start()
                    raw_path = Path(tempfile.mkstemp(
                        prefix="meetingscribe_rec_", suffix=".raw", dir=str(self._workdir))[1])
                    engines.append({"kind": "legacy", "source": src, "raw_path": raw_path,
                                     "raw_file": open(raw_path, "wb"), "frames": 0})
        except RecordingError:
            self._teardown_engines(engines)
            raise
        except OSError as e:
            self._teardown_engines(engines)
            raise RecordingError(f"Could not create a temporary recording file: {e}") from e

        self._engines = engines
        self._start_time = time.monotonic()
        self._stop_flag.clear()
        self._mix_thread = threading.Thread(target=self._mix_loop, daemon=True)
        self._mix_thread.start()

    @staticmethod
    def _teardown_engines(engines: list) -> None:
        for e in engines:
            if e["kind"] == "ffmpeg_mic":
                e["capture"].finish()
                e["meter"].stop()
            else:
                e["source"].stop()
                try:
                    e["raw_file"].close()
                except Exception:
                    pass

    def _mix_loop(self):
        while not self._stop_flag.is_set():
            time.sleep(_CHUNK_SECONDS)
            self._drain_and_meter()
        self._drain_and_meter()  # final drain since the last tick

    def pause(self) -> None:
        """ffmpeg-owned sources end their current segment (resume starts a
        new one - see _FfmpegMicCapture); legacy sources just stop being
        written to their raw file (see _drain_and_meter) - either way the
        elapsed-time counter freezes too."""
        if self._paused_since is None:
            self._paused_since = time.monotonic()
            for e in self._engines:
                if e["kind"] == "ffmpeg_mic":
                    e["capture"].pause()

    def resume(self) -> None:
        if self._paused_since is not None:
            self._paused_accum += time.monotonic() - self._paused_since
            self._paused_since = None
            for e in self._engines:
                if e["kind"] == "ffmpeg_mic":
                    e["capture"].resume()

    @property
    def is_paused(self) -> bool:
        return self._paused_since is not None

    def _drain_and_meter(self):
        paused = self._paused_since is not None
        peaks = []
        for e in self._engines:
            if e["kind"] == "ffmpeg_mic":
                peaks.append(e["meter"].take_peak())
                continue
            block = e["source"].drain()
            peaks.append(float(np.abs(block).max()) if block.size else 0.0)
            if not paused and block.size:
                try:
                    e["raw_file"].write(block.astype(np.float32, copy=False).tobytes())
                    e["frames"] += block.shape[0]
                except Exception:
                    pass  # already closed by stop()
        self._events.put(("levels", peaks))

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

        wall_clock_duration = self.elapsed_seconds()

        arrays = []
        for e in self._engines:
            if e["kind"] == "ffmpeg_mic":
                e["meter"].stop()
                segments = e["capture"].finish()
                mic_wav_path = self._workdir / "mic_final.wav"
                _concat_wav_segments(segments, mic_wav_path, self._channels)
                arrays.append(_load_wav_as_array(mic_wav_path, self._channels))
            else:
                # Device-clock span, when available (microphone), bypasses
                # Python-side wall-clock jitter for the rate measurement -
                # see _SourceStream.device_duration and module docstring.
                device_duration = e["source"].device_duration()
                e["source"].stop()
                try:
                    e["raw_file"].close()
                except Exception:
                    pass
                active_duration = (max(0.0, device_duration - self._paused_accum)
                                    if device_duration is not None else wall_clock_duration)
                arrays.append(self._load_and_resample(e["raw_path"], e["frames"], active_duration))

        self._engines = []
        mixed = arrays[0] if len(arrays) == 1 else self._mix_final(arrays)
        self._write_wav(mixed)
        if self._workdir is not None:
            shutil.rmtree(self._workdir, ignore_errors=True)
            self._workdir = None
        return self._out_path

    def _load_and_resample(self, path: Path, frames_written: int, active_duration: float) -> np.ndarray:
        if frames_written == 0 or active_duration <= 0:
            return np.zeros((0, self._channels), dtype=np.float32)
        raw = np.fromfile(path, dtype=np.float32).reshape(-1, self._channels)
        true_rate = frames_written / active_duration
        if not (8000 <= true_rate <= 192000):
            return raw  # implausible measurement - use as captured rather than distort it further
        # resample_poly (polyphase FIR, proper anti-aliasing), not the
        # FFT-based resample(): the latter treats the signal as one
        # periodic cycle, which is wrong for real, non-periodic speech and
        # shows up as ringing/artifacts - resample_poly is the function
        # actually meant for real-world audio like this.
        ratio = Fraction(_TARGET_RATE / true_rate).limit_denominator(10_000)
        return resample_poly(raw, ratio.numerator, ratio.denominator, axis=0).astype(np.float32)

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
