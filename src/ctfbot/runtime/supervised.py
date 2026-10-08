"""Durable create/start ownership for a single offline Docker solver."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from ctfbot.runtime.docker import DockerRuntime, RuntimeErrorSafe
from ctfbot.runtime.service_recovery import (
    daemon_identity, private_directory, process_start_ticks, read_private, remove_owned, requests_settled,
    state_lock, unfinished, write_private,
)


REMOTE_NETWORK_PROFILE = "offline-remote-tcp-v1"


class SupervisedOfflineRuntime(DockerRuntime):
    def __init__(self, root: Path, image: str, *, recovery_root: Path,
                 expected_daemon: dict[str, Any], event_sink: Callable[[dict[str, Any]], None]) -> None:
        super().__init__(root, image)
        self.owner = self.container_name.removeprefix("ctfbot-")
        self._recovery_root = recovery_root
        self._expected_daemon = expected_daemon
        self._runtime_sink = event_sink
        self._journal: Path | None = None
        self._admission_lock: Any = None
        self._cleanup_lock = threading.RLock()
        self._workers: list[subprocess.Popen[bytes]] = []

    def _runtime_event(self, state: str, **details: Any) -> None:
        self._runtime_sink({"state": state, "solver_container": self.container_name,
                            "network_profile": REMOTE_NETWORK_PROFILE,
                            "recovery_journal": str(self._journal) if self._journal else None, **details})

    def _admit(self) -> None:
        with self._cleanup_lock:
            self._startup_check()
            root = private_directory(self._recovery_root)
            lock = state_lock(root)
            lock.__enter__()
            try:
                identity = daemon_identity()
                if identity != self._expected_daemon:
                    raise RuntimeErrorSafe("Docker identity changed; repeat remote runtime acceptance")
                if unfinished(root):
                    raise RuntimeErrorSafe("unfinished remote recovery blocks admission; run `ctfbot remote recover`")
                journal = private_directory(root / self.owner) / "state.json"
                write_private(journal, {
                    "schema_version": 2, "runtime_profile": REMOTE_NETWORK_PROFILE,
                    "owner": self.owner, "daemon": identity,
                    "resources": [["container", self.container_name]],
                    "controller_pid": os.getpid(), "controller_start_ticks": process_start_ticks(os.getpid()),
                    "controller_connections_pending": False,
                    "cleanup_complete": False, "recovery_required": False,
                })
                self._journal, self._admission_lock = journal, lock
            except Exception:
                lock.__exit__(None, None, None)
                raise

    def _startup_check(self) -> None:
        if self._closed or self._cancel_requested.is_set():
            raise RuntimeErrorSafe("remote solver startup cancelled")

    def _mutation(self, args: list[str], deadline: float) -> None:
        assert self._journal is not None
        request = self._journal.parent / f"request-{uuid.uuid4()}.json"
        record = {"status": "pending", "argv": ["docker", *args]}
        with self._cleanup_lock:
            self._startup_check()
            if time.monotonic() >= deadline:
                raise TimeoutError("remote solver startup budget exhausted")
            write_private(request, record)
            try:
                worker = subprocess.Popen([sys.executable, "-m", "ctfbot.runtime.docker_request", str(request)],
                                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL, start_new_session=True)
            except OSError:
                write_private(request, {**record, "status": "acknowledged", "returncode": 125})
                raise
            self._workers.append(worker)
        while True:
            result = read_private(request)
            if result.get("status") == "acknowledged":
                worker.poll()
                self._startup_check()
                if result.get("returncode") != 0:
                    raise RuntimeErrorSafe(f"Docker remote solver {args[0]} failed")
                return
            if (result.get("status") == "uncertain" or worker.poll() is not None
                    or time.monotonic() >= deadline or self._cancel_requested.is_set()):
                with self._cleanup_lock:
                    state = read_private(self._journal)
                    state["recovery_required"] = True
                    write_private(self._journal, state)
                self._startup_check()
                if time.monotonic() >= deadline:
                    raise TimeoutError("Docker remote solver request exceeded its startup budget; recovery required")
                raise RuntimeErrorSafe("Docker remote solver acknowledgement was lost or uncertain; recovery required")
            time.sleep(0.02)

    def start_supervised(self, deadline: float) -> None:
        self._startup_check()
        if self._started:
            return
        self._admit()
        try:
            self._runtime_event("solver_preparing")
            def inspect(args: list[str]) -> bytes:
                self._startup_check()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("remote solver startup budget exhausted")
                try:
                    result = subprocess.run(["docker", *args], capture_output=True, timeout=min(5, remaining))
                except subprocess.TimeoutExpired as exc:
                    raise TimeoutError("remote solver inspection timed out") from exc
                if result.returncode:
                    raise RuntimeErrorSafe("remote solver inspection failed")
                return result.stdout

            if json.loads(inspect(["image", "inspect", "--format", "{{json .Config.Volumes}}", self.image_digest])) not in (None, {}):
                raise RuntimeErrorSafe("remote solver images with anonymous volumes are unsupported")
            argv = self._create_argv()
            argv[2:2] = ["--label", f"ctfbot.owner={self.owner}"]
            self._mutation(argv[1:], deadline)
            self._mutation(["start", self.container_name], deadline)
            state = json.loads(inspect(["container", "inspect", self.container_name]))[0]
            if (state.get("State", {}).get("Running") is not True
                    or state.get("HostConfig", {}).get("NetworkMode") != "none"
                    or state.get("Config", {}).get("Labels", {}).get("ctfbot.owner") != self.owner):
                raise RuntimeErrorSafe("remote solver did not start with the offline ownership profile")
            self._startup_check()
            self._started = True
            self._runtime_event("solver_ready")
        except Exception:
            self.close()
            raise

    def start(self) -> None:
        self.start_supervised(time.monotonic() + self.startup_timeout)

    def close(self, *, additional_errors: tuple[str, ...] = ()) -> None:
        with self._cleanup_lock:
            self._closed = True
            self._started = False
            if self._journal is None:
                return
            if self._admission_lock is None:
                if read_private(self._journal).get("cleanup_complete") is True and not additional_errors:
                    return
                lock = state_lock(self._recovery_root)
                lock.__enter__()
                self._admission_lock = lock
            errors: list[str] = list(additional_errors)
            try:
                # End controller-side PTYs without an unchecked Docker rm.
                with self._sessions_lock:
                    sessions = tuple(self._sessions.values())
                    self._sessions.clear()
                for session in sessions:
                    try:
                        session.cancel(kill_container=False)
                    except Exception as exc:
                        errors.append(f"session:{type(exc).__name__}")
                try:
                    deadline = time.monotonic() + 2
                    while not requests_settled(self._journal.parent) and time.monotonic() < deadline:
                        time.sleep(0.05)
                    state = read_private(self._journal)
                    state["controller_connections_pending"] = "remote_connection:unacknowledged" in additional_errors
                    # Even while requests are pending, remove resources already owned.
                    remove_owned(state)
                    if not requests_settled(self._journal.parent):
                        errors.append("docker_request:pending_or_uncertain")
                    state.update(cleanup_complete=not errors, recovery_required=bool(errors))
                    write_private(self._journal, state)
                except Exception as exc:
                    errors.append(f"recovery:{type(exc).__name__}")
                    try:
                        state = read_private(self._journal)
                        if additional_errors:
                            state["controller_connections_pending"] = True
                        state.update(cleanup_complete=False, recovery_required=True)
                        write_private(self._journal, state)
                    except Exception:
                        pass  # keep the existing incomplete durable intent
                self._runtime_event("cleanup_failed" if errors else "cleaned", errors=errors)
            finally:
                self._admission_lock.__exit__(None, None, None)
                self._admission_lock = None
                for worker in self._workers:
                    worker.poll()
            if errors:
                raise RuntimeErrorSafe("remote solver cleanup incomplete; run `ctfbot remote recover`")
