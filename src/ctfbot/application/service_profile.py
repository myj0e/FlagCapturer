"""Controller-owned activation of one accepted, immutable local service fixture."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ctfbot.challenge.local_service import LocalServiceSpec
from ctfbot.runtime.local_service import DockerLocalServiceRuntime
from ctfbot.runtime.service_recovery import (
    NETWORK_PROFILE, daemon_identity, private_directory, read_private, state_lock, unfinished, write_private,
)


FIXTURE_ARGV = ["/usr/local/bin/python3", "/opt/ctfbot-fixture/server.py"]
_CASES = {"solve": "candidate_unverified", "cancel": "user_cancelled", "cancel_startup": "user_cancelled",
          "provider_failure": "provider_error", "readiness_timeout": "error",
          "run_timeout": "budget_exhausted", "service_exit": "error"}
_PROFILE_FIELDS = {"schema_version", "network_profile", "daemon", "service_image", "solver_image",
                   "service_argv", "service_port", "authorization_basis", "acceptance_path", "acceptance_sha256"}


def service_profile_path() -> Path:
    return Path.cwd() / "data" / "service-profile.json"


def approve_service_profile(acceptance_path: Path, basis: str, *, destination: Path | None = None) -> Path:
    if not isinstance(basis, str) or not basis.strip() or len(basis.encode()) > 4096:
        raise ValueError("a nonempty review/authorization basis of at most 4 KiB is required")
    receipt = read_private(acceptance_path)
    identity = daemon_identity()
    if (receipt.get("result") != "PASS" or receipt.get("schema_version") != 2
            or receipt.get("daemon") != identity or receipt.get("network_profile") != NETWORK_PROFILE):
        raise ValueError("acceptance is missing, outdated, or belongs to a different Docker host")
    network = receipt.get("network", {})
    required_probes = {"host", "bridge_address", "cross_run", "public_ipv4", "public_ipv6",
                       "dns:127.0.0.11", "dns:8.8.8.8"}
    if not isinstance(network, dict) or set(network) != {f"run_{number}_{role}" for number in (1, 2) for role in ("solver", "service")}:
        raise ValueError("acceptance is missing a service or solver network result")
    for probes in network.values():
        if (not isinstance(probes, dict) or set(probes) != required_probes
                or any(value != "blocked" for key, value in probes.items() if key != "dns:127.0.0.11")
                or probes["dns:127.0.0.11"] not in {"blocked", "no_external_answer"}):
            raise ValueError("network isolation acceptance is incomplete")
    lifecycle = receipt.get("lifecycle", {})
    if not isinstance(lifecycle, dict):
        raise ValueError("missing lifecycle acceptance")
    for case, expected in _CASES.items():
        result = lifecycle.get(case, {})
        if not isinstance(result, dict) or result.get("status") != expected or result.get("removed") is not True:
            raise ValueError(f"missing successful lifecycle acceptance: {case}")
    recovery = receipt.get("recovery", {})
    if not isinstance(recovery, dict) or set(recovery) != {"network_create", "container_create", "container_start"} or any(
        not isinstance(result, dict) or result.get("status") != "cleaned" or result.get("blocked_while_pending") is not True
        for result in recovery.values()
    ):
        raise ValueError("Docker timeout recovery acceptance is incomplete")
    from ctfbot.runtime.docker import validate_image_reference
    image = validate_image_reference(receipt.get("image"))
    if receipt.get("service_argv") != FIXTURE_ARGV or receipt.get("service_port") != 31337:
        raise ValueError("this activation increment accepts only the authored fixture protocol")
    path = destination or service_profile_path()
    private_directory(path.parent)
    root = path.parent / "service-state"
    with state_lock(root):
        if unfinished(root):
            raise ValueError("recover unfinished service runs before replacing an execution profile")
        write_private(path, {
            "schema_version": 1, "network_profile": NETWORK_PROFILE, "daemon": identity,
            "service_image": image, "solver_image": image, "service_argv": FIXTURE_ARGV,
            "service_port": 31337, "authorization_basis": basis.strip(),
            "acceptance_path": str(acceptance_path.resolve()),
            "acceptance_sha256": hashlib.sha256(acceptance_path.read_bytes()).hexdigest(),
        })
    return path


class ReviewedServiceFactory:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or service_profile_path()
        self.recovery_root = self.path.parent / "service-state"

    def validate(self, spec: LocalServiceSpec, solver_image: str, *, workspace: Path | None = None,
                 runs_root: Path | None = None) -> dict[str, Any]:
        if not self.path.exists():
            raise ValueError("No reviewed local service profile; run `ctfbot service approve` after acceptance.")
        for protected in (workspace, runs_root):
            if protected is not None and (self.path.resolve().is_relative_to(protected.resolve())
                                           or self.recovery_root.resolve().is_relative_to(protected.resolve())):
                raise ValueError("service profile and recovery state must remain outside challenge/run directories")
        profile = read_private(self.path)
        if (set(profile) != _PROFILE_FIELDS or type(profile["schema_version"]) is not int or profile["schema_version"] != 1
                or profile["network_profile"] != NETWORK_PROFILE
                or not isinstance(profile["authorization_basis"], str) or not profile["authorization_basis"].strip()):
            raise ValueError("invalid reviewed local service execution profile")
        acceptance_path = profile["acceptance_path"]
        if not isinstance(acceptance_path, str):
            raise ValueError("execution profile must reference its private acceptance record")
        acceptance = Path(acceptance_path)
        receipt = read_private(acceptance)
        if (hashlib.sha256(acceptance.read_bytes()).hexdigest() != profile["acceptance_sha256"]
                or receipt.get("schema_version") != 2 or receipt.get("result") != "PASS"
                or receipt.get("network_profile") != profile["network_profile"]
                or receipt.get("daemon") != profile["daemon"]
                or receipt.get("image") != profile["service_image"]
                or receipt.get("image") != profile["solver_image"]
                or receipt.get("service_argv") != profile["service_argv"]
                or receipt.get("service_port") != profile["service_port"]):
            raise ValueError("execution profile or acceptance changed; repeat reviewed activation")
        if (profile["service_image"] != spec.image or profile["solver_image"] != solver_image
                or profile["service_argv"] != list(spec.argv) or profile["service_port"] != spec.port):
            raise ValueError("service image, solver image, command and port must match the reviewed profile")
        if profile["daemon"] != daemon_identity():
            raise ValueError("Docker host/profile changed; repeat local service acceptance")
        if unfinished(self.recovery_root):
            raise ValueError("unfinished service recovery blocks admission; run `ctfbot service recover`")
        return profile

    def __call__(self, root: Path, image: str, spec: LocalServiceSpec, sink):
        profile = self.validate(spec, image, workspace=root)
        profile_hash = hashlib.sha256(self.path.read_bytes()).hexdigest()

        def record(details):
            sink({**details, "execution_profile_sha256": profile_hash,
                  "execution_profile": str(self.path.resolve()),
                  "acceptance_sha256": profile["acceptance_sha256"]})

        return DockerLocalServiceRuntime(root, image, spec, event_sink=record,
                                         recovery_root=self.recovery_root, expected_daemon=profile["daemon"])
