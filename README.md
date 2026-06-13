# AudioProcessor

Local speech-to-text with speaker diarization and known-speaker recognition.
Everything runs on your own machine via [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
and [pyannote.audio](https://github.com/pyannote/pyannote-audio) — no cloud APIs,
no audio ever leaves the host.

Built for meeting and conversation recordings, primarily in **Ukrainian** and
**Russian**, with automatic language detection.

## How it works

1. You drop audio/video files into the `input/` folder and run the tool.
2. faster-whisper transcribes the speech.
3. pyannote `speaker-diarization-3.1` splits the audio into speaker turns.
4. Each turn is turned into a voice embedding and matched against a local
   speaker database, so known people get their real names in the transcript.
5. Unrecognized speakers are shown with sample lines; you can name them and
   optionally save their voiceprint for next time.
6. The result is written as plain text to the `output/` folder.

If no HuggingFace token is configured, diarization is unavailable and the tool
falls back to a plain transcript with timestamps.

## Features

- Fully local — runs offline once models are downloaded; no audio uploaded anywhere.
- Speaker diarization (who spoke when) via pyannote `speaker-diarization-3.1`.
- Known-speaker recognition against a local, versioned voiceprint database.
- On-the-fly enrollment: name an unknown speaker and save their voiceprint
  (`Name (v1).npy`, `Name (v2).npy`, …), with overwrite / add-version / skip prompts.
- Automatic language detection (tuned for mixed Ukrainian/Russian).
- CPU or NVIDIA CUDA, auto-detected.
- Skips files already transcribed in `output/` (asks before overwriting), so
  re-running a batch does not redo finished work.
- Plain-text output, easy to read and diff.

## Requirements

- Windows 10/11 (the `.bat` launchers are Windows-oriented; the Python scripts
  themselves are cross-platform).
- Python 3.10+ (`setup.bat` installs Python 3.12 if missing).
- [ffmpeg](https://ffmpeg.org/) (installed automatically by `setup.bat`).
- Python dependencies from `requirements.txt` (faster-whisper, pyannote.audio,
  torch, numpy, huggingface_hub, ffmpeg-python).
- A [HuggingFace](https://huggingface.co/) account and access token **for
  diarization** (optional — without it the tool runs as plain transcription).
- Optional: an NVIDIA GPU with CUDA for faster processing (auto-detected;
  `setup.bat` installs the CUDA build of torch if a GPU is found).

## Install

Run the one-time setup from the project folder:

```bat
setup.bat
```

It checks/installs Python, ffmpeg and the Python dependencies, detects an NVIDIA
GPU and installs the CUDA build of torch if present, prepares `config.env`,
reminds you about the HuggingFace model licenses, and downloads the models. It
ends with a summary and waits for a keypress.

To (re-)download models separately:

```bat
download_models.bat
```

`download_models.py` supports `--all` (mandatory + every optional, no prompts),
`--mandatory` (mandatory only), and an interactive default. Run
non-interactively without a flag, it behaves as `--mandatory`.

## Configuration (HuggingFace token)

Diarization uses gated pyannote models, which require a HuggingFace token with
the model licenses accepted.

1. Copy the template to create your local config:

   ```bat
   copy config.env.example config.env
   ```

   (`setup.bat` does this for you if `config.env` is missing.)

2. Get a token at <https://hf.co/settings/tokens> and put it in `config.env`:

   ```
   HF_TOKEN=hf_your_token_here
   ```

   `HF_TOKEN` can also be set as an environment variable, which takes precedence.

3. Accept the license for each gated pyannote model while logged in to
   HuggingFace:
   - <https://hf.co/pyannote/speaker-diarization-3.1>
   - <https://hf.co/pyannote/segmentation-3.0>
   - <https://hf.co/pyannote/embedding>

`config.env` is git-ignored and must never be committed with a real token.

## Usage

1. Put your audio/video files in the `input/` folder.
2. Run:

   ```bat
   run.bat
   ```

3. Pick the file(s), recognition model, language, and whether to diarize.
4. For unrecognized speakers, optionally type a name and save their voiceprint.
5. Find the transcript in `output/` (one `.txt` per input file).

## Supported input formats

`.webm` `.mp4` `.mkv` `.mov` `.avi` `.m4a` `.mp3` `.wav`

## Output format

Plain text, one block per speaker turn:

```
[Speaker name or Speaker N] mm:ss
spoken text for this turn...

[Another speaker] mm:ss
their spoken text...
```

If diarization is unavailable, the tool falls back to a continuous transcript
with periodic timestamps.

## Models and licenses

No model weights are stored in this repository — they are downloaded separately
by `download_models.py` into the local `models/` folder, **each under its own
license**:

- **pyannote** models (`speaker-diarization-3.1`, `segmentation-3.0`,
  `wespeaker-voxceleb-resnet34-LM`, `embedding`) are **gated** and require
  accepting their license on HuggingFace with a valid token (see Configuration).
- **Whisper** models (`faster-whisper-large-v3-turbo`, and optionally
  `faster-whisper-large-v3`) are downloaded from their respective HuggingFace
  repositories under their own terms.

**Mandatory set:** the four pyannote models above plus Whisper `large-v3-turbo`
(minimal recognition model). **Optional set:** Whisper `large-v3` for higher
accuracy at the cost of size and speed.

You are responsible for complying with the license terms of each model you
download.

## License

This project's source code is released under the [MIT License](LICENSE). Model
weights are **not** covered by this license — see "Models and licenses" above.
