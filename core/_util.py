"""Small shared infrastructure helpers with no single natural home."""

import contextlib
import os
import sys
import warnings


@contextlib.contextmanager
def silence():
    """Suppress stdout/stderr (fd level + Python streams + warnings) - some ML
    libraries (pyannote.audio, torch) print noisy, user-irrelevant banners."""
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    saved_out = os.dup(1)
    saved_err = os.dup(2)
    os.dup2(devnull_fd, 1)
    os.dup2(devnull_fd, 2)
    os.close(devnull_fd)
    dn_out = open(os.devnull, "w")
    dn_err = open(os.devnull, "w")
    old_stdout, sys.stdout = sys.stdout, dn_out
    old_stderr, sys.stderr = sys.stderr, dn_err
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            yield
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        dn_out.close()
        dn_err.close()
        os.dup2(saved_out, 1)
        os.dup2(saved_err, 2)
        os.close(saved_out)
        os.close(saved_err)


@contextlib.contextmanager
def hf_offline():
    """Forces huggingface_hub (and anything built on it, e.g. pyannote.audio)
    to use only its local cache, no network. pyannote.audio's own
    Pipeline.from_pretrained has no local_files_only kwarg (confirmed absent
    from its signature in 4.0.7 - passing it just raises TypeError), so this
    env var is the only version-agnostic way to force that."""
    prev = os.environ.get("HF_HUB_OFFLINE")
    os.environ["HF_HUB_OFFLINE"] = "1"
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("HF_HUB_OFFLINE", None)
        else:
            os.environ["HF_HUB_OFFLINE"] = prev
