"""Reviewed activation of one explicitly granted remote TCP fixture scope."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ctfbot.challenge.remote import RemoteSpec
from ctfbot.runtime.remote import CandidateRemoteFactory, DockerRemoteRuntime
from ctfbot.runtime.service_recovery import (
    daemon_identity, private_directory, read_private, state_lock, unfinished, write_private,
)
from ctfbot.runtime.supervised import REMOTE_NETWORK_PROFILE


_PROFILE_FIELDS = {"schema_version", "network_profile", "daemon", "solver_image", "grant_sha256",
                   "acceptance_path", "acceptance_sha256", "authorization_basis"}
REQUIRED_CASES = {
    "solve": "verified",
    "wrong_host": "denied", "wrong_port": "denied", "wrong_protocol": "denied",
    "literal_ip_mismatch": "denied", "missing_grant": "denied", "expired_grant": "denied",
    "input_limit": "denied", "output_limit": "bounded",
    "shell_egress": "blocked", "session_egress": "blocked",
    "dns_rebinding": "pinned", "redirect": "not_followed", "proxy_environment": "ignored",
    "tool_timeout": "closed", "run_timeout": "closed", "scope_expiry": "closed",
    "cancel": "closed", "connection_refused": "closed", "provider_failure": "cleaned",
    "cleanup_failure": "blocked", "container_create_recovery": "cleaned",
    "container_start_recovery": "cleaned", "controller_crash_recovery": "cleaned",
    "pending_admission": "blocked", "lost_acknowledgement": "blocked",
    "stage_b_c_regression": "passed", "tui_solve_entrypoints": "passed",
}


def remote_profile_path() -> Path:
    return Path.cwd() / "data" / "remote-profile.json"


def remote_grant_path() -> Path:
    return Path.cwd() / "data" / "remote-grant.json"


def _receipt(path: Path, *, identity: dict[str, Any], image: str, grant_hash: str) -> dict[str, Any]:
    receipt = read_private(path)
    if (type(receipt.get("schema_version")) is not int or receipt.get("schema_version") != 1
            or receipt.get("result") != "PASS" or receipt.get("network_profile") != REMOTE_NETWORK_PROFILE
            or receipt.get("daemon") != identity or receipt.get("solver_image") != image
            or receipt.get("grant_sha256") != grant_hash):
        raise ValueError("remote acceptance is missing, outdated, or does not match the host/image/grant")
    cases = receipt.get("cases")
    if not isinstance(cases, dict) or set(cases) != set(REQUIRED_CASES):
        raise ValueError("remote acceptance must include all required boundary and lifecycle cases")
    for name, expected in REQUIRED_CASES.items():
        case = cases[name]
        if (not isinstance(case, dict) or case.get("status") != expected
                or case.get("resources_removed") is not True
                or not isinstance(case.get("evidence"), str) or not case["evidence"].strip()):
            raise ValueError(f"remote acceptance is incomplete: {name}")
    return receipt


def approve_remote_profile(acceptance_path: Path, grant_path: Path, basis: str, *,
                           destination: Path | None = None) -> Path:
    if not isinstance(basis, str) or not basis.strip() or len(basis.encode()) > 4096:
        raise ValueError("review requires a nonempty authorization basis of at most 4 KiB")
    path = destination or remote_profile_path()
    if path.resolve() == grant_path.resolve() or path.parent.resolve() != grant_path.parent.resolve():
        raise ValueError("remote profile and grant must be separate files in the same private controller directory")
    grant = read_private(grant_path)
    spec, image = RemoteSpec.from_manifest(grant.get("remote")), grant.get("solver_image")
    _, grant_hash, _ = CandidateRemoteFactory(grant_path)._load(spec, image)
    identity = daemon_identity()
    _receipt(acceptance_path, identity=identity, image=image, grant_hash=grant_hash)
    if acceptance_path.resolve() in {path.resolve(), grant_path.resolve()}:
        raise ValueError("acceptance, profile and grant must be separate files")
    private_directory(path.parent)
    with state_lock(path.parent / "remote-state"):
        if unfinished(path.parent / "remote-state"):
            raise ValueError("recover unfinished remote runs before replacing the profile")
        write_private(path, {
            "schema_version": 1, "network_profile": REMOTE_NETWORK_PROFILE, "daemon": identity,
            "solver_image": image, "grant_sha256": grant_hash,
            "acceptance_path": str(acceptance_path.resolve()),
            "acceptance_sha256": hashlib.sha256(acceptance_path.read_bytes()).hexdigest(),
            "authorization_basis": basis.strip(),
        })
    return path


class ReviewedRemoteFactory:
    def __init__(self, path: Path | None = None, grant_path: Path | None = None) -> None:
        self.path = path or remote_profile_path()
        self.grant_path = grant_path or self.path.parent / "remote-grant.json"
        self.recovery_root = self.path.parent / "remote-state"

    def validate(self, spec: RemoteSpec, image: str, *, workspace: Path | None = None,
                 runs_root: Path | None = None) -> dict[str, Any]:
        if not self.path.exists():
            raise ValueError("No reviewed remote profile; C4 acceptance and `ctfbot remote approve` are required.")
        if self.path.resolve() == self.grant_path.resolve() or self.path.parent.resolve() != self.grant_path.parent.resolve():
            raise ValueError("remote profile and grant must be separate files in the same private directory")
        for root in (workspace, runs_root):
            if root is not None and any(path.resolve().is_relative_to(root.resolve())
                                        for path in (self.path, self.recovery_root)):
                raise ValueError("remote controller configuration must remain outside workspace/runs")
        profile = read_private(self.path)
        if (set(profile) != _PROFILE_FIELDS or type(profile["schema_version"]) is not int
                or profile["schema_version"] != 1 or profile["network_profile"] != REMOTE_NETWORK_PROFILE
                or not isinstance(profile["authorization_basis"], str) or not profile["authorization_basis"].strip()):
            raise ValueError("invalid reviewed remote profile")
        _, grant_hash, _ = CandidateRemoteFactory(self.grant_path)._load(spec, image, workspace=workspace, runs_root=runs_root)
        identity = daemon_identity()
        if (profile["grant_sha256"] != grant_hash or profile["solver_image"] != image
                or profile["daemon"] != identity):
            raise ValueError("remote grant, image or Docker identity changed; repeat reviewed activation")
        if not isinstance(profile["acceptance_path"], str):
            raise ValueError("remote profile must reference a private acceptance record")
        acceptance = Path(profile["acceptance_path"])
        _receipt(acceptance, identity=identity, image=image, grant_hash=grant_hash)
        if hashlib.sha256(acceptance.read_bytes()).hexdigest() != profile["acceptance_sha256"]:
            raise ValueError("remote acceptance changed; repeat reviewed activation")
        return profile

    def __call__(self, root: Path, image: str, spec: RemoteSpec, sink):
        profile = self.validate(spec, image, workspace=root.parent)
        ip, grant_hash, basis = CandidateRemoteFactory(self.grant_path)._load(spec, image, workspace=root.parent)
        if grant_hash != profile["grant_sha256"]:
            raise ValueError("remote grant changed during runtime preparation")
        def record(details):
            sink({"endpoint": spec.endpoint, "grant_sha256": grant_hash,
                  "execution_profile": str(self.path.resolve()),
                  "acceptance_sha256": profile["acceptance_sha256"], **details})
        record({"state": "authorized", "pinned_ip": ip, "not_before": spec.not_before.isoformat(),
                "not_after": spec.not_after.isoformat(), "controller_authorization_basis": basis})
        return DockerRemoteRuntime(root, image, spec=spec, pinned_ip=ip, grant_sha256=grant_hash,
                                   event_sink=record, recovery_root=self.recovery_root,
                                   expected_daemon=profile["daemon"])
