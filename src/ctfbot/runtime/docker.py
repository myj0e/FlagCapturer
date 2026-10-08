"""Persistent Docker command session with a conservative offline profile.

This adapter is intentionally not auto-started by the CLI. A Docker daemon,
approved isolated host, and a digest-pinned tooling image are required.
"""

from __future__ import annotations

import os
import pty
import re
import subprocess
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable

from ctfbot.runtime.sessions import InteractiveReadResult, InteractiveSession
from ctfbot.runtime.command_supervisor import SUPERVISOR, SUPERVISOR_SHELL
from ctfbot.tools.registry import CommandResult


_IMAGE_DIGEST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:+-]*@sha256:[0-9a-f]{64}$")
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_INTERACTIVE_SESSIONS = 4
_SESSION_IDLE_TIMEOUT = 60.0
_SESSION_TOTAL_TIMEOUT = 600.0
_SESSION_OUTPUT_BYTES = 1024 * 1024


class RuntimeErrorSafe(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DockerLimits:
    cpus: float = 2.0
    memory: str = "4g"
    memory_swap: str = "4g"
    pids: int = 256
    work_tmpfs: str = "2g"
    command_output_bytes: int = 1024 * 1024


def docker_create_argv(*, image_digest: str, challenge_root: Path, container_name: str,
                       limits: DockerLimits = DockerLimits()) -> list[str]:
    validate_image_reference(image_digest)
    root = challenge_root.resolve(strict=True)
    if not root.is_dir() or challenge_root.is_symlink():
        raise ValueError("challenge root must be a regular directory, not a symlink")
    source = str(root)
    if "," in source or "\n" in source or "\r" in source:
        raise ValueError("challenge root path contains characters unsupported by Docker --mount")
    if not re.fullmatch(r"ctfbot-[a-f0-9-]{36}", container_name):
        raise ValueError("container name must be a ctfbot-generated UUID name")
    if not (0 < limits.cpus <= 2.0) or limits.pids <= 0 or limits.command_output_bytes <= 0:
        raise ValueError("runtime limits exceed the Stage A profile")
    if limits.memory not in {"512m", "1g", "2g", "4g"} or limits.memory_swap != limits.memory:
        raise ValueError("memory must use a supported cap with swap disabled")
    if limits.pids > 256 or limits.work_tmpfs not in {"256m", "512m", "1g", "2g"}:
        raise ValueError("PID or workdir limit exceeds the Stage A profile")
    if hasattr(os, "getuid") and (os.getuid() == 0 or (hasattr(os, "geteuid") and os.geteuid() == 0)):
        raise ValueError("Docker runtime must be launched by a non-root controller")
    uid = os.getuid() if hasattr(os, "getuid") else 1000
    gid = os.getgid() if hasattr(os, "getgid") else 1000
    return [
        "docker", "create", "--name", container_name, "--pull=never",
        "--network=none", "--read-only", "--cpus", str(limits.cpus),
        "--memory", limits.memory, "--memory-swap", limits.memory_swap,
        "--pids-limit", str(limits.pids), "--user", f"{uid}:{gid}",
        "--cap-drop=ALL", "--security-opt=no-new-privileges:true",
        "--env", "HOME=/tmp", "--env", "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "--tmpfs", f"/tmp:rw,nosuid,nodev,size=64m,uid={uid},gid={gid}",
        "--tmpfs", f"/work:rw,exec,nosuid,nodev,size={limits.work_tmpfs},uid={uid},gid={gid},mode=0700",
        "--mount", f"type=bind,source={source},target=/challenge,readonly",
        "--workdir", "/work", "--entrypoint", "/bin/sh", image_digest,
        "-c", "while :; do sleep 3600; done",
    ]


def validate_image_reference(image_digest: str) -> str:
    """Validate a local immutable image identifier without contacting Docker."""
    if not isinstance(image_digest, str) or not (
        _IMAGE_DIGEST.fullmatch(image_digest) or _IMAGE_ID.fullmatch(image_digest)
    ):
        raise ValueError("runtime image must be pinned as repository@sha256:<digest> or immutable sha256:<image ID>")
    return image_digest


class DockerRuntime:
    """One container per model run; its writable workdir is size-capped tmpfs."""

    def __init__(self, challenge_root: Path, image_digest: str, *, docker: str = "docker",
                 limits: DockerLimits = DockerLimits(), startup_timeout: float = 30) -> None:
        self.challenge_root = challenge_root
        self.image_digest = image_digest
        self.docker = docker
        self.limits = limits
        self.startup_timeout = startup_timeout
        self.container_name = f"ctfbot-{uuid.uuid4()}"
        self._started = False
        self._closed = False
        self._cancel_requested = threading.Event()
        self._process_lock = threading.Lock()
        self._active_process: subprocess.Popen[bytes] | None = None
        self._sessions_lock = threading.RLock()
        self._sessions: dict[str, InteractiveSession] = {}

    def start(self) -> None:
        if self._cancel_requested.is_set():
            raise RuntimeErrorSafe("sandbox startup was cancelled")
        if self._started:
            return
        create = self._create_argv()
        create[0] = self.docker
        try:
            created = subprocess.run(create, capture_output=True, timeout=self.startup_timeout, check=False)
            if created.returncode:
                raise RuntimeErrorSafe("Docker could not create the sandbox container: " + _safe_output(created.stderr))
            if self._cancel_requested.is_set():
                self.close()
                raise RuntimeErrorSafe("sandbox startup was cancelled")
            started = subprocess.run([self.docker, "start", self.container_name], capture_output=True,
                                     timeout=self.startup_timeout, check=False)
            if started.returncode:
                raise RuntimeErrorSafe("Docker could not start the sandbox container: " + _safe_output(started.stderr))
            if self._cancel_requested.is_set():
                self.close()
                raise RuntimeErrorSafe("sandbox startup was cancelled")
            state = subprocess.run([self.docker, "inspect", "--format", "{{.State.Running}}", self.container_name],
                                   capture_output=True, timeout=self.startup_timeout, check=False)
            if state.returncode or state.stdout.strip() != b"true":
                raise RuntimeErrorSafe("sandbox container did not remain running; check that the pinned image has /bin/sh and sleep")
            self._started = True
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.close()
            raise RuntimeErrorSafe(f"Docker startup failed: {type(exc).__name__}") from exc
        except Exception:
            self.close()
            raise

    def _create_argv(self) -> list[str]:
        return docker_create_argv(
            image_digest=self.image_digest,
            challenge_root=self.challenge_root,
            container_name=self.container_name,
            limits=self.limits,
        )

    def execute(self, argv: list[str], *, timeout: float) -> CommandResult:
        if self._closed:
            raise RuntimeErrorSafe("sandbox session is closed")
        if self._cancel_requested.is_set():
            raise RuntimeErrorSafe("sandbox session was cancelled")
        if not argv or len(argv) > 64 or any(not isinstance(part, str) or "\x00" in part for part in argv):
            raise ValueError("command must be a non-empty NUL-free argv list")
        if not 0 < timeout <= 120:
            raise ValueError("command timeout must be between 0 and 120 seconds")
        if not self._started:
            self.start()
        marker = "CTFBOT_COMMAND_" + uuid.uuid4().hex
        command = [self.docker, "exec", "--workdir", "/work", self.container_name,
                   "/bin/sh", "-c", SUPERVISOR_SHELL, "ctfbot-command",
                   SUPERVISOR, str(timeout), marker, *argv]
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as exc:
            raise RuntimeErrorSafe(f"could not invoke Docker exec: {type(exc).__name__}") from exc
        with self._process_lock:
            self._active_process = process
            cancelled_during_start = self._cancel_requested.is_set()
        if cancelled_during_start:
            try:
                process.kill()
            except OSError:
                pass
            self._kill_container()
            process.wait(timeout=5)
            raise RuntimeErrorSafe("sandbox session was cancelled")
        stdout = _BoundedReader(process.stdout, self.limits.command_output_bytes)
        stderr = _BoundedReader(process.stderr, self.limits.command_output_bytes)
        stdout.start()
        stderr.start()
        try:
            # The in-container supervisor enforces the command budget. Allow a
            # bounded grace for descendant cleanup and Docker transport teardown.
            process.wait(timeout=max(0.1, timeout) + 5)
        except subprocess.TimeoutExpired:
            self._kill_container()
            try:
                process.kill()
            except OSError:
                pass
            process.wait(timeout=5)
            stdout.join(timeout=5)
            stderr.join(timeout=5)
            with self._process_lock:
                self._active_process = None
            return CommandResult(
                exit_code=124,
                stdout=stdout.data,
                stderr=stderr.data,
                timed_out=True,
                truncated=stdout.truncated or stderr.truncated,
            )
        stdout.join(timeout=5)
        stderr.join(timeout=5)
        if stdout.is_alive() or stderr.is_alive():
            self._kill_container()
            with self._process_lock:
                self._active_process = None
            raise RuntimeErrorSafe("sandbox output streams did not close after command completion")
        with self._process_lock:
            self._active_process = None
        trailer = re.search(rb"\n" + marker.encode() + rb":(ok|timeout|cleanup_failed|legacy):(-?\d+)\n$", stderr.tail)
        timed_out = False
        exit_code = process.returncode if process.returncode is not None else 125
        if trailer:
            state = trailer.group(1)
            exit_code = int(trailer.group(2))
            timed_out = state == b"timeout"
            if stderr.data.endswith(trailer.group(0)):
                stderr.data = stderr.data[:-len(trailer.group(0))]
            if state == b"cleanup_failed":
                self._kill_container()
                raise RuntimeErrorSafe("command descendant cleanup failed; sandbox closed")
        else:
            self._kill_container()
            raise RuntimeErrorSafe("command supervisor did not confirm completion; sandbox closed")
        return CommandResult(
            exit_code=exit_code,
            stdout=stdout.data,
            stderr=stderr.data,
            timed_out=timed_out,
            truncated=stdout.truncated or stderr.truncated,
        )

    @property
    def closed(self) -> bool:
        return self._closed

    def start_interactive(
        self,
        session_id: str,
        argv: list[str],
        *,
        on_output: Callable[[bytes], None],
        on_closed: Callable[[str, int | None], None],
        idle_timeout: float = _SESSION_IDLE_TIMEOUT,
        total_timeout: float = _SESSION_TOTAL_TIMEOUT,
    ) -> None:
        if self._closed or self._cancel_requested.is_set():
            raise RuntimeErrorSafe("sandbox session is closed")
        try:
            if str(uuid.UUID(session_id)) != session_id:
                raise ValueError
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("interactive session ID must be a canonical UUID") from exc
        _validate_argv(argv)
        with self._sessions_lock:
            if session_id in self._sessions:
                raise ValueError("interactive session ID is already in use")
            if len(self._sessions) >= _MAX_INTERACTIVE_SESSIONS:
                raise ValueError("the run has reached its interactive session limit")
        if not self._started:
            self.start()
        command = [
            self.docker, "exec", "--interactive", "--tty", "--workdir", "/work",
            self.container_name, *argv,
        ]
        try:
            master_fd, slave_fd = pty.openpty()
        except OSError as exc:
            raise RuntimeErrorSafe(f"could not allocate interactive terminal: {type(exc).__name__}") from exc
        try:
            process = subprocess.Popen(
                command,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=subprocess.STDOUT,
                bufsize=0,
            )
        except OSError as exc:
            os.close(master_fd)
            raise RuntimeErrorSafe(f"could not start interactive Docker session: {type(exc).__name__}") from exc
        finally:
            try:
                os.close(slave_fd)
            except OSError:
                pass
        input_stream = None
        output_stream = None
        try:
            # Docker CLI rejects --tty when its stdin is a pipe. Give the CLI
            # the slave side and retain the master as the session's full-duplex
            # controller stream.
            input_stream = os.fdopen(os.dup(master_fd), "wb", buffering=0)
            output_stream = os.fdopen(os.dup(master_fd), "rb", buffering=0)
        except OSError as exc:
            if input_stream is not None:
                input_stream.close()
            if output_stream is not None:
                output_stream.close()
            try:
                os.close(master_fd)
            except OSError:
                pass
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.wait(timeout=1)
            except (subprocess.TimeoutExpired, OSError):
                pass
            self._kill_container()
            raise RuntimeErrorSafe(f"could not attach interactive terminal: {type(exc).__name__}") from exc
        process.stdin = input_stream
        process.stdout = output_stream
        os.close(master_fd)
        with self._sessions_lock:
            if self._cancel_requested.is_set() or self._closed:
                try:
                    process.kill()
                except OSError:
                    pass
                try:
                    process.wait(timeout=1)
                except (subprocess.TimeoutExpired, OSError):
                    pass
                if process.stdin is not None:
                    process.stdin.close()
                if process.stdout is not None:
                    process.stdout.close()
                self._kill_container()
                raise RuntimeErrorSafe("sandbox session was cancelled during startup")
            session = InteractiveSession(
                session_id,
                process,
                idle_timeout=idle_timeout,
                total_timeout=total_timeout,
                output_limit=_SESSION_OUTPUT_BYTES,
                on_output=on_output,
                on_closed=on_closed,
                on_force_close=self._kill_container,
            )
            self._sessions[session_id] = session

    def start_script_session(self, session_id: str, argv: list[str], *,
                             total_timeout: float, on_output: Callable[[bytes], None],
                             on_closed: Callable[[str, int | None], None]) -> None:
        """Long script: model decides on silence; supervisor bounds total time."""
        _validate_argv(argv)
        if not 0 < total_timeout <= 1800:
            raise ValueError("script session total timeout must be within the run budget")
        def finished(status: str, code: int | None) -> None:
            if code == 125:
                self._kill_container()
            on_closed(status, code)
        self.start_interactive(
            session_id, ["python3", "-I", "-u", "-c", SUPERVISOR,
                         str(total_timeout), "session", *argv],
            on_output=on_output, on_closed=finished,
            idle_timeout=1800, total_timeout=min(1800, total_timeout + 5),
        )

    def send_interactive(self, session_id: str, data: bytes) -> None:
        session = self._interactive(session_id)
        if not data or len(data) > 8 * 1024 or b"\x00" in data:
            raise ValueError("interactive input must contain 1 to 8192 NUL-free bytes")
        session.send(data)

    def read_interactive(self, session_id: str, *, timeout: float, maximum_bytes: int) -> InteractiveReadResult:
        session = self._interactive(session_id)
        return session.read(timeout=timeout, maximum_bytes=maximum_bytes)

    def close_interactive(self, session_id: str) -> InteractiveReadResult:
        with self._sessions_lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise ValueError("interactive session does not exist in this run")
        result = session.close()
        with self._sessions_lock:
            self._sessions.pop(session_id, None)
        return result

    def _interactive(self, session_id: str) -> InteractiveSession:
        with self._sessions_lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise ValueError("interactive session does not exist in this run")
        return session

    def _kill_container(self) -> None:
        try:
            subprocess.run([self.docker, "kill", self.container_name], capture_output=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass
        self._closed = True

    def cancel(self) -> None:
        """Kill an active docker exec process and the sandbox so the run can stop promptly."""
        self._cancel_requested.set()
        with self._sessions_lock:
            sessions = tuple(self._sessions.values())
        for session in sessions:
            session.cancel(kill_container=False)
        # Record cancellation before killing the container closes its PTYs.
        # Otherwise the reader can classify a requested stop as a normal exit.
        self._kill_container()
        with self._process_lock:
            process = self._active_process
        if process is not None:
            try:
                process.kill()
            except OSError:
                pass

    def close(self) -> None:
        if self._closed and not self._started:
            return
        with self._sessions_lock:
            sessions = tuple(self._sessions.values())
        for session in sessions:
            try:
                if self._closed:
                    session.cancel(kill_container=False)
                else:
                    session.close()
            except Exception:
                self._kill_container()
        with self._sessions_lock:
            self._sessions.clear()
        try:
            subprocess.run([self.docker, "rm", "-f", self.container_name], capture_output=True, timeout=15, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass
        self._closed = True
        self._started = False

    def __enter__(self) -> "DockerRuntime":
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class _BoundedReader(threading.Thread):
    def __init__(self, stream: object, maximum: int) -> None:
        super().__init__(daemon=True)
        self.stream = stream
        self.maximum = maximum
        self.data = b""
        self.truncated = False
        self.tail = b""

    def run(self) -> None:
        if self.stream is None:
            return
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = self.stream.read(65536)  # type: ignore[attr-defined]
            if not chunk:
                break
            self.tail = (self.tail + chunk)[-512:]
            remaining = self.maximum - total
            if remaining > 0:
                chunks.append(chunk[:remaining])
                total += min(len(chunk), remaining)
            if len(chunk) > remaining:
                self.truncated = True
        self.data = b"".join(chunks)
        try:
            self.stream.close()  # type: ignore[attr-defined]
        except OSError:
            pass


def _safe_output(value: bytes) -> str:
    return value[-2000:].decode("utf-8", errors="replace").replace("\x00", "?")


def _validate_argv(argv: list[str]) -> None:
    if not argv or len(argv) > 64 or any(not isinstance(part, str) or "\x00" in part for part in argv):
        raise ValueError("command must be a non-empty NUL-free argv list")
    if sum(len(part.encode("utf-8")) for part in argv) > 16 * 1024:
        raise ValueError("command argv exceeds the 16 KiB limit")
