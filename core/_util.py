"""Small shared infrastructure helpers with no single natural home."""

import contextlib
import os
import sys
import warnings


@contextlib.contextmanager
def silence():
    """Suppress stdout/stderr at OS fd level + Python stream level + warnings.

    Several ML libraries (pyannote.audio, torch) print noisy banners on
    import or model load that have no user-facing value.
    """
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
