"""Thread-safe cancellation control for one foreground run."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class RunControl:
    """Request cancellation promptly and run blocking cleanup hooks off the caller thread."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._callbacks: list[Callable[[], None]] = []
        self._callback_threads: set[threading.Thread] = set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> bool:
        """Set the cancellation flag once and schedule registered interrupt hooks."""
        with self._lock:
            if self._event.is_set():
                return False
            self._event.set()
            callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            self._start_callback(callback)
        return True

    def add_cancel_callback(self, callback: Callable[[], None]) -> None:
        with self._lock:
            already_cancelled = self._event.is_set()
            if not already_cancelled:
                self._callbacks.append(callback)
        if already_cancelled:
            self._start_callback(callback)

    def wait_for_callbacks(self, timeout: float | None = None) -> bool:
        """Wait for interrupt hooks after the run worker has unblocked and begun teardown."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._lock:
                threads = tuple(self._callback_threads)
            if not threads:
                return True
            for thread in threads:
                remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
                thread.join(remaining)
            if deadline is not None and time.monotonic() >= deadline:
                with self._lock:
                    return not self._callback_threads

    def _start_callback(self, callback: Callable[[], None]) -> None:
        def invoke() -> None:
            try:
                callback()
            except Exception:
                # A failed interrupt hook must not crash the UI or skip normal teardown.
                pass
            finally:
                with self._lock:
                    self._callback_threads.discard(threading.current_thread())

        thread = threading.Thread(target=invoke, name="ctfbot-run-cancel", daemon=True)
        with self._lock:
            self._callback_threads.add(thread)
            thread.start()
