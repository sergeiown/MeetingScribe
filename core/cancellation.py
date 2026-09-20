"""Thread-safe cooperative cancellation, shared by any long-running core call."""

import threading
from typing import Callable


class Cancelled(Exception):
    """Raised when a cooperative cancellation was requested."""


class CancelToken:
    """Thread-safe cooperative cancellation flag.

    A GUI's Cancel button can call .cancel() directly from the GUI thread -
    threading.Event.set() is thread-safe and touches no Qt object.
    """

    def __init__(self):
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        """Call at each cooperative checkpoint; raises Cancelled() if requested."""
        if self._event.is_set():
            raise Cancelled()


ProgressFn = Callable[[float, float, str], None]  # (current, total, label)
StatusFn = Callable[[str], None]  # coarse-grained stage text
