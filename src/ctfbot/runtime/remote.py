"""Candidate C4 adapter: offline solver plus a controller-owned TCP connector.

No DNS lookup, HTTP redirect processing, proxy environment or model-supplied IP
is used. Default entrypoints do not install this adapter before live acceptance.
"""

from __future__ import annotations

import errno
import hashlib
import ipaddress
import json
import select
import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ctfbot.challenge.remote import RemoteSpec
from ctfbot.runtime.docker import validate_image_reference
from ctfbot.runtime.service_recovery import daemon_identity, read_private, unfinished
from ctfbot.runtime.supervised import SupervisedOfflineRuntime


class CandidateRemoteFactory:
    """Explicit dependency injection for synthetic acceptance, never auto-loaded."""

    def __init__(self, profile_path: Path) -> None:
        self.profile_path = profile_path

    def _load(self, spec: RemoteSpec, image: str, *, workspace: Path | None = None,
              runs_root: Path | None = None) -> tuple[str, str, str]:
        path = self.profile_path.resolve(strict=True)
        if self.profile_path.is_symlink() or any(path.is_relative_to(root.resolve())
                                                for root in (workspace, runs_root) if root is not None):
            raise ValueError("remote grant must be outside challenge and run directories")
        recovery_root = self.profile_path.parent / "remote-state"
        if any(recovery_root.resolve().is_relative_to(root.resolve()) for root in (workspace, runs_root) if root is not None):
            raise ValueError("remote recovery state must be outside challenge and run directories")
        if unfinished(recovery_root):
            raise ValueError("unfinished remote recovery blocks admission; run `ctfbot remote recover`")
        if path.stat().st_size > 65536:
            raise ValueError("controller remote grant exceeds 64 KiB")
        # One read binds the canonical JSON evidence hash to the validated grant.
        profile = read_private(self.profile_path)
        fields = {"schema_version", "remote", "pinned_ip", "solver_image", "authorization_basis"}
        if set(profile) != fields or type(profile["schema_version"]) is not int or profile["schema_version"] != 1:
            raise ValueError("unsupported controller remote grant")
        if RemoteSpec.from_manifest(profile["remote"]) != spec:
            raise PermissionError("challenge scope does not match the controller remote grant")
        validate_image_reference(image)
        if profile["solver_image"] != image:
            raise PermissionError("remote grant does not authorize this solver image")
        basis = profile["authorization_basis"]
        if not isinstance(basis, str) or not basis.strip() or len(basis) > 4096:
            raise ValueError("controller remote grant requires its own authorization basis")
        ip = profile["pinned_ip"]
        if not isinstance(ip, str):
            raise ValueError("remote IP pin must be a string")
        address = ipaddress.ip_address(ip)
        if str(address) != ip or "%" in ip or address.is_unspecified or address.is_multicast:
            raise ValueError("remote grant requires one canonical unicast IP")
        try:
            host_address = ipaddress.ip_address(spec.host)
        except ValueError:
            host_address = None
        if host_address is not None and host_address != address:
            raise PermissionError("literal host differs from the pinned IP")
        if not spec.not_before <= datetime.now(timezone.utc) < spec.not_after:
            raise PermissionError("remote authorization is outside its time window")
        digest = hashlib.sha256(json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return ip, digest, basis

    def validate(self, spec: RemoteSpec, image: str, **paths: Any) -> None:
        self._load(spec, image, **paths)

    def __call__(self, root: Path, image: str, spec: RemoteSpec,
                 sink: Callable[[dict[str, Any]], None]) -> DockerRemoteRuntime:
        ip, digest, basis = self._load(spec, image, workspace=root.parent)
        sink({"state": "authorized", "endpoint": spec.endpoint, "pinned_ip": ip,
              "grant_sha256": digest, "not_before": spec.not_before.isoformat(),
              "not_after": spec.not_after.isoformat(), "solver_image": image,
              "controller_authorization_basis": basis})
        return DockerRemoteRuntime(root, image, spec=spec, pinned_ip=ip,
                                   grant_sha256=digest, event_sink=sink,
                                   recovery_root=self.profile_path.parent / "remote-state",
                                   expected_daemon=daemon_identity())


class DockerRemoteRuntime(SupervisedOfflineRuntime):
    def __init__(self, root: Path, image: str, *, spec: RemoteSpec, pinned_ip: str,
                 grant_sha256: str, event_sink: Callable[[dict[str, Any]], None],
                 recovery_root: Path, expected_daemon: dict[str, Any]) -> None:
        def runtime_event(details: dict[str, Any]) -> None:
            event_sink({"endpoint": spec.endpoint, "grant_sha256": grant_sha256, **details})
        super().__init__(root, image, recovery_root=recovery_root,
                         expected_daemon=expected_daemon, event_sink=runtime_event)
        self.remote_spec = spec
        self._ip = pinned_ip
        self._grant_sha256 = grant_sha256
        self._sink = event_sink
        self._remote_stopped = threading.Event()
        self._remote_lock = threading.Condition()
        self._remote_sockets: set[socket.socket] = set()
        self._run_deadline = float("inf")
        self._scope_deadline = time.monotonic() + max(0, (spec.not_after - datetime.now(timezone.utc)).total_seconds())

    def set_startup_deadline(self, deadline: float) -> None:
        self._run_deadline = deadline

    def start(self) -> None:
        self._check(self._run_deadline)
        self.start_supervised(min(time.monotonic() + self.startup_timeout, self._run_deadline, self._scope_deadline))
        self._check(self._run_deadline)

    def _startup_check(self) -> None:
        super()._startup_check()
        self._check(self._run_deadline)

    def _check(self, deadline: float) -> None:
        if self._remote_stopped.is_set():
            raise RuntimeError("remote connector has been stopped")
        if not self.remote_spec.not_before <= datetime.now(timezone.utc) < self.remote_spec.not_after:
            raise PermissionError("remote authorization expired or has not started")
        if time.monotonic() >= min(deadline, self._run_deadline, self._scope_deadline):
            raise TimeoutError("remote exchange exhausted its time budget")

    def remote_exchange(self, host: str, port: int, protocol: str, data: bytes, *,
                        timeout: float, maximum_bytes: int) -> dict[str, Any]:
        if not self._started or self._closed:
            raise RuntimeError("remote solver must be admitted and running before connecting")
        spec = self.remote_spec
        if (host, port, protocol) != (spec.host, spec.port, spec.protocol):
            raise PermissionError("remote endpoint is outside the controller allowlist")
        if not isinstance(data, bytes) or len(data) > 16384 or not 0 < timeout <= 30 or not 1 <= maximum_bytes <= 16384:
            raise ValueError("remote exchange exceeds its bounded input/output/time limits")
        deadline = time.monotonic() + timeout
        self._check(deadline)
        sock = socket.socket(socket.AF_INET6 if ":" in self._ip else socket.AF_INET, socket.SOCK_STREAM)
        sock.setblocking(False)
        with self._remote_lock:
            if self._remote_stopped.is_set():
                sock.close()
                raise RuntimeError("remote connector has been stopped")
            if self._remote_sockets:
                sock.close()
                raise RuntimeError("only one remote exchange may be active per run")
            self._remote_sockets.add(sock)
        received = bytearray()
        sent = 0
        outcome = "error"
        try:
            self._sink({"state": "connecting", "endpoint": spec.endpoint, "pinned_ip": self._ip,
                        "grant_sha256": self._grant_sha256, "timeout_seconds": timeout,
                        "maximum_bytes": maximum_bytes, "not_after": spec.not_after.isoformat()})
            self._check(deadline)
            code = sock.connect_ex((self._ip, port))
            if code not in (0, errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY):
                raise OSError(code, "remote TCP connect failed")
            while code:
                self._check(deadline)
                _, writable, exceptional = select.select([], [sock], [sock], 0.05)
                if writable or exceptional:
                    code = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                    if code:
                        raise OSError(code, "remote TCP connect failed")
            if ipaddress.ip_address(sock.getpeername()[0]) != ipaddress.ip_address(self._ip):
                raise PermissionError("actual peer differs from the controller IP pin")
            # One connection per call: send the bounded request, half-close, read
            # until EOF, output cap or deadline. No follow-up connection is inferred.
            while sent < len(data):
                self._check(deadline)
                _, writable, _ = select.select([], [sock], [], 0.05)
                if writable:
                    count = sock.send(data[sent:])
                    if not count:
                        raise ConnectionError("remote peer closed while sending")
                    sent += count
            self._check(deadline)
            sock.shutdown(socket.SHUT_WR)
            while len(received) < maximum_bytes:
                self._check(deadline)
                readable, _, _ = select.select([sock], [], [], 0.05)
                if readable:
                    chunk = sock.recv(maximum_bytes - len(received))
                    if not chunk:
                        outcome = "eof"
                        break
                    received.extend(chunk)
            else:
                outcome = "output_limit"
            return {"data": bytes(received), "status": outcome, "sent_bytes": sent}
        except TimeoutError:
            outcome = "timed_out"
            return {"data": bytes(received), "status": outcome, "sent_bytes": sent}
        finally:
            with self._remote_lock:
                self._remote_sockets.discard(sock)
                sock.close()
                self._remote_lock.notify_all()
            self._sink({"state": "connection_closed", "endpoint": spec.endpoint,
                        "grant_sha256": self._grant_sha256, "status": outcome,
                        "sent_bytes": sent, "received_bytes": len(received),
                        "request_bytes": data, "response_bytes": bytes(received)})

    def _stop_remote(self) -> None:
        self._remote_stopped.set()
        with self._remote_lock:
            for sock in self._remote_sockets:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                # The exchange owns close(); shutdown wakes select without FD reuse.

    def cancel(self) -> None:
        self._stop_remote()
        super().cancel()

    def close(self) -> None:
        self._stop_remote()
        with self._remote_lock:
            acknowledged = self._remote_lock.wait_for(lambda: not self._remote_sockets, timeout=2)
        super().close(additional_errors=() if acknowledged else ("remote_connection:unacknowledged",))
