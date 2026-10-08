"""Reviewed local Docker services with isolated networking and timeout recovery.

An isolated internal bridge has no host bridge address. External DNS forwarding
is disabled explicitly. Production entrypoints require a reviewed execution profile.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn

from ctfbot.challenge.local_service import LocalServiceSpec
from ctfbot.runtime.docker import DockerLimits, DockerRuntime, RuntimeErrorSafe, _BoundedReader, docker_create_argv
from ctfbot.runtime.service_recovery import (
    daemon_identity, private_directory, read_private, requests_settled,
    state_lock, unfinished, write_private,
)


_ISOLATED_GATEWAY = "com.docker.network.bridge.gateway_mode_ipv4"


class ServiceReadinessError(RuntimeErrorSafe):
    pass


class DockerLocalServiceRuntime(DockerRuntime):
    """One service and solver on a run-specific bridge, with bounded TCP readiness."""

    def __init__(self, challenge_root: Path, image_digest: str, spec: LocalServiceSpec, *,
                 event_sink: Callable[[dict[str, Any]], None], docker: str = "docker",
                 recovery_root: Path | None = None, expected_daemon: dict[str, Any] | None = None) -> None:
        super().__init__(challenge_root, image_digest, docker=docker, startup_timeout=5,
                         limits=DockerLimits(cpus=1, memory="2g", memory_swap="2g", pids=128, work_tmpfs="1g"))
        self.spec = spec
        self.event_sink = event_sink
        self.owner = str(uuid.uuid4())
        self.network_name = f"ctfbot-net-{self.owner}"
        self.service_name = f"ctfbot-{uuid.uuid4()}"
        self._network_attempted = False
        self._service_attempted = False
        self._solver_attempted = False
        self._cleanup_lock = threading.RLock()
        self._startup_deadline: float | None = None
        self._run_deadline: float | None = None
        self._recovery_root = recovery_root
        self._expected_daemon = expected_daemon
        self._journal: Path | None = None
        self._admission_lock: Any = None
        self._request_workers: list[subprocess.Popen[bytes]] = []

    def _admit(self) -> None:
        if self._recovery_root is None or self._journal is not None:
            return
        if self.docker != "docker":
            raise ValueError("supervised service requests require the local Docker CLI")
        root = private_directory(self._recovery_root)
        self._admission_lock = state_lock(root)
        try:
            self._admission_lock.__enter__()
        except Exception:
            self._admission_lock = None
            raise
        try:
            identity = daemon_identity()
            if identity != self._expected_daemon:
                raise RuntimeErrorSafe("Docker host/profile changed; repeat local service acceptance")
            if unfinished(root):
                raise RuntimeErrorSafe("unfinished service recovery blocks admission; run `ctfbot service recover`")
            owner_dir = private_directory(root / self.owner)
            self._journal = owner_dir / "state.json"
            write_private(self._journal, {
                "schema_version": 1, "owner": self.owner, "daemon": identity,
                "resources": [["container", self.container_name], ["container", self.service_name],
                              ["network", self.network_name]],
                "cleanup_complete": False, "recovery_required": False,
            })
        except Exception:
            self._admission_lock.__exit__(None, None, None)
            self._admission_lock = None
            raise

    def _supervised_request(self, args: list[str], timeout: float) -> subprocess.CompletedProcess[bytes]:
        assert self._journal is not None
        command = [self.docker, *args]
        request = self._journal.parent / f"request-{uuid.uuid4()}.json"
        record = {"status": "pending", "argv": command}
        with self._cleanup_lock:
            self._check_cancelled()
            write_private(request, record)  # durable intent before the Docker client starts
            try:
                worker = subprocess.Popen([sys.executable, "-m", "ctfbot.runtime.docker_request", str(request)],
                                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL, start_new_session=True)
            except OSError:
                write_private(request, {**record, "status": "acknowledged", "returncode": 125})
                raise
        self._request_workers.append(worker)
        deadline = time.monotonic() + timeout
        while True:
            result = read_private(request)
            if result["status"] == "acknowledged":
                worker.poll()
                return subprocess.CompletedProcess(command, result["returncode"],
                                                   result.get("stdout", "").encode(),
                                                   result.get("stderr", "").encode())
            if result["status"] == "uncertain" or worker.poll() is not None or time.monotonic() >= deadline:
                state = read_private(self._journal)
                state["recovery_required"] = True
                write_private(self._journal, state)
                raise subprocess.TimeoutExpired(command, timeout)
            time.sleep(min(0.02, max(0, deadline - time.monotonic())))

    def set_startup_deadline(self, deadline: float) -> None:
        self._run_deadline = deadline

    def _startup_timeout(self) -> NoReturn:
        if self._run_deadline is not None and self._startup_deadline == self._run_deadline:
            raise TimeoutError("run wall-time budget exhausted during service startup")
        raise ServiceReadinessError("local service did not become ready within its startup budget")

    def _emit(self, state: str, **details: Any) -> None:
        self.event_sink({"state": state, "service_image": self.spec.image,
                         "network_profile": "isolated-ipv4-no-upstream-dns-v1",
                         "endpoint": self.spec.endpoint, "network": self.network_name,
                         "service_container": self.service_name,
                         "solver_container": self.container_name, **details})

    def _check_cancelled(self) -> None:
        if self._cancel_requested.is_set() or self._closed:
            raise RuntimeErrorSafe("local service startup was cancelled")

    def _docker(self, args: list[str], *, timeout: float = 5) -> subprocess.CompletedProcess[bytes]:
        if self._startup_deadline is not None:
            remaining = self._startup_deadline - time.monotonic()
            if remaining <= 0:
                self._startup_timeout()
            timeout = min(timeout, remaining)
        try:
            mutating = args[0] in {"create", "start"} or args[:2] == ["network", "create"]
            if self._journal is not None and mutating:
                result = self._supervised_request(args, timeout)
            else:
                result = subprocess.run([self.docker, *args], capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            if self._startup_deadline is not None and time.monotonic() >= self._startup_deadline:
                self._startup_timeout()
            raise
        if result.returncode:
            raise RuntimeErrorSafe(f"Docker local service operation failed: {args[0]}")
        return result

    def _create_argv(self) -> list[str]:
        argv = super()._create_argv()
        argv[argv.index("--network=none")] = f"--network={self.network_name}"
        argv[2:2] = ["--label", f"ctfbot.owner={self.owner}",
                     "--dns", "127.0.0.1", "--dns-search", "."]
        return argv

    def start(self) -> None:
        self._check_cancelled()
        if self._started:
            return
        self._admit()
        deadline = time.monotonic() + self.spec.startup_timeout_seconds
        if self._run_deadline is not None:
            deadline = min(deadline, self._run_deadline)
        self._startup_deadline = deadline
        try:
            self._emit("preparing", recovery_journal=str(self._journal) if self._journal else None)
            for image_ref in {self.spec.image, self.image_digest}:
                image = self._docker(["image", "inspect", "--format", "{{json .Config.Volumes}}", image_ref])
                if json.loads(image.stdout) not in (None, {}):
                    raise RuntimeErrorSafe("service/solver images declaring anonymous volumes are unsupported")
            self._check_cancelled()
            self._network_attempted = True
            try:
                self._docker(["network", "create", "--driver", "bridge", "--internal",
                              "--ipv6=false", "--opt", f"{_ISOLATED_GATEWAY}=isolated",
                              "--label", f"ctfbot.owner={self.owner}", self.network_name])
            finally:
                self._network_attempted = True
            self._check_cancelled()
            network = json.loads(self._docker(["network", "inspect", self.network_name]).stdout)[0]
            if (network.get("Internal") is not True or network.get("Driver") != "bridge"
                    or network.get("EnableIPv6") is not False
                    or network.get("Options", {}).get(_ISOLATED_GATEWAY) != "isolated"
                    or network.get("Labels", {}).get("ctfbot.owner") != self.owner):
                raise RuntimeErrorSafe("Docker did not create the requested isolated IPv4 service network")
            self._emit("network_created")
            service_argv = docker_create_argv(
                image_digest=self.spec.image, challenge_root=self.challenge_root,
                container_name=self.service_name,
                limits=DockerLimits(cpus=1, memory="1g", memory_swap="1g", pids=64, work_tmpfs="256m"),
            )
            service_argv[0] = self.docker
            service_argv[service_argv.index("--network=none")] = f"--network={self.network_name}"
            service_argv[2:2] = ["--label", f"ctfbot.owner={self.owner}", "--network-alias", "challenge",
                                  "--dns", "127.0.0.1", "--dns-search", ".",
                                  "--no-healthcheck", "--log-driver=local", "--log-opt", "max-size=1m",
                                  "--log-opt", "max-file=1", "--log-opt", "compress=false"]
            entrypoint = service_argv.index("--entrypoint")
            service_argv[entrypoint:] = ["--entrypoint", self.spec.argv[0], self.spec.image, *self.spec.argv[1:]]
            self._check_cancelled()
            self._service_attempted = True
            try:
                self._docker(service_argv[1:])
            finally:
                self._service_attempted = True
            self._check_cancelled()
            self._docker(["start", self.service_name])
            self._emit("service_started")
            self._check_cancelled()
            self._solver_attempted = True
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._startup_timeout()
                self.startup_timeout = min(5, remaining / 3)
                if self._journal is None:
                    super().start()
                else:
                    self._docker(self._create_argv()[1:])
                    self._check_cancelled()
                    self._docker(["start", self.container_name])
                    self._check_cancelled()
                    state = self._docker(["inspect", "--format", "{{.State.Running}}", self.container_name])
                    if state.stdout.strip() != b"true":
                        raise RuntimeErrorSafe("solver did not remain running")
                    self._started = True
            finally:
                self._solver_attempted = True
            while time.monotonic() < deadline:
                self._check_cancelled()
                state = self._docker(["inspect", "--format", "{{.State.Running}}", self.service_name])
                if state.stdout.strip() != b"true":
                    raise ServiceReadinessError("challenge service exited before readiness")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                # Probe from the solver's network namespace, never from the host.
                probe = super().execute([
                    "python3", "-c",
                    "import socket,sys; socket.create_connection((sys.argv[1],int(sys.argv[2])),timeout=0.5).close()",
                    "challenge", str(self.spec.port),
                ], timeout=min(1, remaining))
                if probe.timed_out:
                    break  # command timeouts retire the solver container
                if probe.exit_code == 0:
                    self._check_cancelled()
                    if time.monotonic() >= deadline:
                        self._startup_timeout()
                    self._emit("healthy", healthcheck="tcp_from_solver")
                    return
                self._cancel_requested.wait(min(0.1, max(0, deadline - time.monotonic())))
            self._startup_timeout()
        except Exception as exc:
            try:
                self._emit("startup_failed", error_type=type(exc).__name__)
            finally:
                self.close()
            raise
        finally:
            self._startup_deadline = None

    def _labels(self, kind: str, name: str) -> dict[str, Any] | None:
        template = "{{json .Labels}}" if kind == "network" else "{{json .Config.Labels}}"
        result = subprocess.run([self.docker, kind, "inspect", "--format", template, name],
                                capture_output=True, timeout=5, check=False)
        if result.returncode:
            message = result.stderr.decode("utf-8", errors="replace").lower()
            missing = (
                f"no such object: {name}", f"no such container: {name}",
                f"no such network: {name}", f"network {name} not found",
            )
            if any(expected in message for expected in missing):
                return None
            raise RuntimeErrorSafe(f"could not confirm {kind} cleanup state")
        labels = json.loads(result.stdout)
        if not isinstance(labels, dict) or labels.get("ctfbot.owner") != self.owner:
            raise RuntimeErrorSafe(f"refusing to remove a {kind} not owned by this run")
        return labels

    def _collect_logs(self) -> None:
        process = subprocess.Popen([self.docker, "logs", "--tail", "100", self.service_name],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        reader = _BoundedReader(process.stdout, 64 * 1024)
        reader.start()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1)
        reader.join(timeout=1)
        if reader.is_alive():
            raise RuntimeErrorSafe("service log output stream did not close")
        self._emit("logs_collected", log_bytes=reader.data, truncated=reader.truncated,
                   exit_code=process.returncode)

    def close(self) -> None:
        with self._cleanup_lock:
            errors: list[str] = []
            if self._journal is not None and self._admission_lock is None:
                try:
                    if read_private(self._journal).get("cleanup_complete") is True:
                        self._closed = True
                        self._started = False
                        return
                except (OSError, ValueError) as exc:
                    errors.append(f"recovery_journal:{type(exc).__name__}")
                assert self._recovery_root is not None
                self._admission_lock = state_lock(self._recovery_root)
                try:
                    self._admission_lock.__enter__()
                except Exception:
                    self._admission_lock = None
                    raise
            self._startup_deadline = None  # cleanup has its own bounded operations
            if self._journal is not None:
                # A timed-out create can complete after an initial absence check.
                # Wait briefly for acknowledgement; never clear its intent early.
                try:
                    deadline = time.monotonic() + 2
                    while not requests_settled(self._journal.parent) and time.monotonic() < deadline:
                        time.sleep(0.05)
                    if not requests_settled(self._journal.parent):
                        errors.append("docker_request:pending_or_uncertain")
                except (OSError, ValueError) as exc:
                    errors.append(f"recovery_journal:{type(exc).__name__}")
            for flag, kind, name in (
                ("_solver_attempted", "container", self.container_name),
                ("_service_attempted", "container", self.service_name),
                ("_network_attempted", "network", self.network_name),
            ):
                if not getattr(self, flag):
                    continue
                try:
                    labels = self._labels(kind, name)
                    if labels is not None:
                        if flag == "_solver_attempted":
                            super().close()
                            # The base offline adapter makes teardown best-effort;
                            # service mode must confirm deletion and allow retry.
                            if self._labels(kind, name) is not None:
                                self._docker([kind, "rm", "-f", name])
                        else:
                            if flag == "_service_attempted":
                                try:
                                    self._collect_logs()
                                except Exception as exc:
                                    try:
                                        self._emit("log_collection_failed", error_type=type(exc).__name__)
                                    except Exception:
                                        pass  # evidence failure must not skip resource removal
                            self._docker([kind, "rm", *(["-f"] if kind == "container" else []), name])
                        if self._labels(kind, name) is not None:
                            raise RuntimeErrorSafe(f"{kind} remained after cleanup")
                    if not errors:
                        setattr(self, flag, False)
                except Exception as exc:
                    errors.append(f"{kind}:{type(exc).__name__}")
            self._closed = True
            self._started = False
            try:
                if self._journal is not None:
                    try:
                        state = read_private(self._journal)
                        state.update(cleanup_complete=not errors, recovery_required=bool(errors))
                        write_private(self._journal, state)
                    except (OSError, ValueError) as exc:
                        errors.append(f"recovery_journal:{type(exc).__name__}")
                self._emit("cleanup_failed" if errors else "cleaned", errors=errors,
                           recovery_journal=str(self._journal) if self._journal else None)
            finally:
                if self._admission_lock is not None:
                    self._admission_lock.__exit__(None, None, None)
                    self._admission_lock = None
                for worker in self._request_workers:
                    worker.poll()
            if errors:
                raise RuntimeErrorSafe("local service cleanup failed; inspect recorded resource names")

    def cancel(self) -> None:
        self._cancel_requested.set()
        super().cancel()
        self.close()
