"""Bounded controller-side handles for interactive processes inside one run."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True, slots=True)
class InteractiveReadResult:
    data: bytes
    status: str
    exit_code: int | None
    timed_out: bool = False
    truncated: bool = False
    elapsed_seconds: float = 0.0
    output_idle_seconds: float = 0.0


class InteractiveSession:
    """Read output continuously, cap its transcript, and enforce idle/total time."""

    def __init__(
        self,
        session_id: str,
        process: subprocess.Popen[bytes],
        *,
        idle_timeout: float,
        total_timeout: float,
        output_limit: int,
        on_output: Callable[[bytes], None],
        on_closed: Callable[[str, int | None], None],
        on_force_close: Callable[[], None],
    ) -> None:
        self.session_id = session_id
        self.process = process
        self.idle_timeout = idle_timeout
        self.total_timeout = total_timeout
        self.output_limit = output_limit
        self.on_output = on_output
        self.on_closed = on_closed
        self.on_force_close = on_force_close
        self._started = time.monotonic()
        self._last_activity = self._started
        self._last_output = self._started
        self._condition = threading.Condition()
        self._write_lock = threading.Lock()
        self._pending = bytearray()
        self._received = 0
        self._status = "running"
        self._exit_code: int | None = None
        self._timed_out = False
        self._truncated = False
        self._finished = False
        self._reader = threading.Thread(target=self._read_output, name=f"ctfbot-session-{session_id[:8]}", daemon=True)
        self._watchdog = threading.Thread(target=self._watch, name=f"ctfbot-session-watch-{session_id[:8]}", daemon=True)
        self._reader.start()
        self._watchdog.start()

    @property
    def status(self) -> str:
        with self._condition:
            return self._status

    def send(self, data: bytes) -> None:
        with self._condition:
            if self._finished:
                raise RuntimeError(f"interactive session is {self._status}")
            self._last_activity = time.monotonic()
            self._condition.notify_all()
        if self.process.stdin is None:
            raise RuntimeError("interactive session input is unavailable")
        try:
            with self._write_lock:
                self.process.stdin.write(data)
                self.process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            raise RuntimeError("interactive session input closed") from exc

    def read(self, *, timeout: float, maximum_bytes: int) -> InteractiveReadResult:
        if not 0 <= timeout <= 10 or not 0 < maximum_bytes <= 16 * 1024:
            raise ValueError("interactive reads must wait at most 10 seconds and return at most 16 KiB")
        deadline = time.monotonic() + timeout
        with self._condition:
            self._last_activity = time.monotonic()
            self._condition.notify_all()
            while not self._pending and not self._finished:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)
            size = min(len(self._pending), maximum_bytes)
            data = bytes(self._pending[:size])
            del self._pending[:size]
            return InteractiveReadResult(
                data=data,
                status=self._status,
                exit_code=self._exit_code,
                timed_out=self._timed_out,
                truncated=self._truncated,
                elapsed_seconds=round(time.monotonic() - self._started, 3),
                output_idle_seconds=round(time.monotonic() - self._last_output, 3),
            )

    def close(self, *, graceful_timeout: float = 1.5) -> InteractiveReadResult:
        with self._condition:
            if not self._finished:
                self._last_activity = time.monotonic()
        if not self._finished:
            self._write_control(b"\x04")
            if not self._wait(graceful_timeout / 2):
                self._write_control(b"\x03")
            if not self._wait(graceful_timeout / 2):
                self._force("forced_closed", timed_out=False)
        self._reader.join(timeout=1)
        self._watchdog.join(timeout=1)
        return self.read(timeout=0, maximum_bytes=16 * 1024)

    def cancel(self, *, kill_container: bool = True) -> None:
        self._force("cancelled", timed_out=False, kill_container=kill_container)
        self._reader.join(timeout=1)
        self._watchdog.join(timeout=1)

    def _read_output(self) -> None:
        stream = self.process.stdout
        if stream is None:
            self._finish("exited", self._confirmed_exit_code())
            return
        fd = stream.fileno()
        try:
            while True:
                chunk = os.read(fd, 4096)
                if not chunk:
                    self._finish("exited", self._confirmed_exit_code())
                    return
                with self._condition:
                    self._last_output = time.monotonic()
                    remaining = self.output_limit - self._received
                    accepted = chunk[:max(0, remaining)]
                    overflow = len(accepted) < len(chunk)
                    self._received += len(accepted)
                    if overflow:
                        self._truncated = True
                if accepted:
                    try:
                        self.on_output(accepted)
                    except Exception:
                        self._force("transcript_error", timed_out=False)
                        return
                    with self._condition:
                        self._pending.extend(accepted)
                        self._condition.notify_all()
                if overflow:
                    self._force("output_limit", timed_out=False)
                    return
        except (OSError, ValueError):
            self._finish("exited", self._confirmed_exit_code())

    def _confirmed_exit_code(self) -> int | None:
        # PTY EOF/EIO can precede Docker CLI exit. Do not publish a terminal
        # state with a missing exit code merely because poll() raced that exit.
        try:
            return self.process.wait(timeout=1)
        except (subprocess.TimeoutExpired, OSError):
            self._force("transport_error", timed_out=False)
            return self.process.poll()

    def _watch(self) -> None:
        while True:
            with self._condition:
                if self._finished:
                    return
                now = time.monotonic()
                remaining_idle = self.idle_timeout - (now - self._last_activity)
                remaining_total = self.total_timeout - (now - self._started)
                remaining = min(remaining_idle, remaining_total)
                if remaining <= 0:
                    reason = "idle_timeout" if remaining_idle <= remaining_total else "total_timeout"
                    break
                self._condition.wait(min(remaining, 0.5))
        self._force(reason, timed_out=True)

    def _write_control(self, data: bytes) -> None:
        stream = self.process.stdin
        if stream is None:
            return
        try:
            with self._write_lock:
                stream.write(data)
                stream.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass

    def _wait(self, timeout: float) -> bool:
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return False
        except (OSError, ValueError):
            return self.process.poll() is not None
        self._reader.join(timeout=1)
        self._finish("closed", self.process.returncode)
        return True

    def _force(self, status: str, *, timed_out: bool, kill_container: bool = True) -> None:
        with self._condition:
            if self._finished:
                return
            self._status = status
            self._timed_out = timed_out
            self._finished = True
            self._condition.notify_all()
        try:
            self.process.kill()
        except (OSError, ProcessLookupError):
            pass
        try:
            if kill_container:
                self.on_force_close()
        finally:
            try:
                self.process.wait(timeout=1)
            except (subprocess.TimeoutExpired, OSError):
                pass
            if threading.current_thread() is not self._reader:
                self._reader.join(timeout=5)
            self._close_streams()
            self._notify_closed(status, self.process.returncode)

    def _finish(self, status: str, exit_code: int | None) -> None:
        with self._condition:
            if self._finished:
                return
            self._status = status
            self._exit_code = exit_code
            self._finished = True
            self._condition.notify_all()
        self._close_streams()
        self._notify_closed(status, exit_code)

    def _close_streams(self) -> None:
        for stream in (self.process.stdin, self.process.stdout):
            if stream is None:
                continue
            try:
                stream.close()
            except OSError:
                pass

    def _notify_closed(self, status: str, exit_code: int | None) -> None:
        try:
            self.on_closed(status, exit_code)
        except Exception:
            # Lifecycle callbacks are best-effort; the runtime still enforces process cleanup.
            pass
