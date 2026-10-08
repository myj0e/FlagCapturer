"""Explicit, reviewed and versioned memory; no automatic cross-run writes."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ctfbot.runtime.service_recovery import private_directory, read_private, write_private

NAMESPACES = ("manuals", "failures", "challenge", "writeups")
_ID = re.compile(r"[0-9a-f]{64}")
_FLAG = re.compile(r"(?i)(?:flag|ctf|ctfbot_synthetic|picoctf|htb)\{[^\n}]{1,4096}\}")


def _hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def _entry(path: Path) -> dict[str, Any]:
    if path.stat().st_size > 65536:
        raise ValueError("memory record exceeds 64 KiB")
    value = read_private(path)
    identity = value.get("id")
    payload = {key: item for key, item in value.items() if key != "id"}
    if (not isinstance(identity, str) or not _ID.fullmatch(identity) or _hash(payload) != identity
            or path.stem != identity or value.get("schema_version") != 1
            or value.get("namespace") not in NAMESPACES or not isinstance(value.get("body"), str)
            or len(value["body"].encode()) > 16384
            or hashlib.sha256(value["body"].encode()).hexdigest() != value.get("body_sha256")):
        raise ValueError("invalid or modified memory record")
    return value


def propose(root: Path, source_file: Path, *, namespace: str, source_url: str, license: str,
            tags: list[str], challenge_id: str | None = None) -> str:
    if namespace not in NAMESPACES or not source_url.strip() or not license.strip():
        raise ValueError("memory requires a valid namespace, source reference and license")
    if source_file.is_symlink() or not source_file.is_file() or source_file.stat().st_size > 16384:
        raise ValueError("memory source must be a regular UTF-8 file of at most 16 KiB")
    if not isinstance(tags, list) or len(tags) > 16 or any(not isinstance(tag, str) or len(tag) > 64 for tag in tags):
        raise ValueError("memory supports at most 16 bounded tags")
    if namespace == "challenge" and not challenge_id:
        raise ValueError("challenge memory must identify its challenge")
    source_bytes = source_file.read_bytes()
    if len(source_bytes) > 16384:
        raise ValueError("memory source grew beyond the 16 KiB limit")
    body = source_bytes.decode("utf-8")
    payload = {"schema_version": 1, "state": "candidate", "namespace": namespace,
               "body": body, "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
               "source_url": source_url, "source_file": str(source_file.resolve()), "license": license,
               "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
               "imported_at": datetime.now(timezone.utc).isoformat(), "tags": tags,
               "challenge_id": challenge_id, "cleanup_status": "active"}
    identity = _hash(payload)
    private_directory(root)
    private_directory(root / "candidates")
    write_private(root / "candidates" / f"{identity}.json", {**payload, "id": identity})
    return identity


def review(root: Path, identity: str, *, reviewer: str, basis: str, decision: str,
           contamination: str) -> str:
    if not _ID.fullmatch(identity) or not reviewer.strip() or not basis.strip() or decision not in {"approve", "reject"}:
        raise ValueError("review requires a candidate ID, reviewer, basis and approve/reject decision")
    candidate = _entry(root / "candidates" / f"{identity}.json")
    if decision == "approve":
        source = Path(candidate["source_file"])
        if (source.is_symlink() or not source.is_file() or source.stat().st_size > 16384
                or hashlib.sha256(source.read_bytes()).hexdigest() != candidate.get("source_sha256")):
            raise ValueError("memory source changed or is unavailable; compare source evidence before approval")
        if candidate["license"].strip().lower() in {"unknown", "unlicensed", "none", "?"}:
            raise ValueError("memory with an unknown license cannot be approved")
    if contamination not in {"generic", "challenge_specific", "answer"}:
        raise ValueError("review must classify answer contamination")
    if decision == "approve" and candidate["namespace"] in {"manuals", "failures"}:
        if contamination != "generic" or _FLAG.search(candidate["body"]):
            raise ValueError("blind-safe memory cannot contain known flag patterns or challenge-specific content")
    record = {key: value for key, value in candidate.items() if key != "id"}
    record.update(state="approved" if decision == "approve" else "rejected", candidate_id=identity,
                  reviewer=reviewer, review_basis=basis, contamination=contamination,
                  reviewed_at=datetime.now(timezone.utc).isoformat())
    version = _hash(record)
    directory = root / ("approved" if decision == "approve" else "rejected")
    private_directory(directory)
    write_private(directory / f"{version}.json", {**record, "id": version})
    return version


def revoke(root: Path, identity: str, *, basis: str) -> None:
    if not _ID.fullmatch(identity) or not basis.strip():
        raise ValueError("revocation requires a version ID and nonempty basis")
    _entry(root / "approved" / f"{identity}.json")
    private_directory(root / "revoked")
    write_private(root / "revoked" / f"{identity}.json", {
        "id": identity, "basis": basis, "revoked_at": datetime.now(timezone.utc).isoformat(),
    })


class MemoryView:
    """Frozen authorized subset for one run; empty by default."""

    def __init__(self, root: Path | None = None, *, namespaces: tuple[str, ...] = (),
                 mode: str = "blind", challenge_id: str | None = None,
                 workspace: Path | None = None, runs_root: Path | None = None) -> None:
        if mode not in {"blind", "practice"} or len(set(namespaces)) != len(namespaces) or any(name not in NAMESPACES for name in namespaces):
            raise ValueError("invalid memory mode/namespaces")
        if mode == "blind" and any(name not in {"manuals", "failures"} for name in namespaces):
            raise PermissionError("blind evaluation permits only reviewed generic manuals/failures")
        if namespaces and root is None:
            raise ValueError("explicit memory namespaces require a controller memory root")
        self.mode, self.namespaces = mode, namespaces
        self.entries: dict[str, dict[str, Any]] = {}
        if not namespaces:
            return
        assert root is not None
        resolved = root.resolve(strict=True)
        for protected in (workspace, runs_root):
            if protected is not None and resolved.is_relative_to(protected.resolve()):
                raise ValueError("memory store must remain outside challenge/run directories")
        if root.is_symlink() or resolved.stat().st_mode & 0o077:
            raise ValueError("memory store must be private and not a symlink")
        if any((root / name).is_symlink() for name in ("approved", "revoked")):
            raise ValueError("memory namespace directories must not be symlinks")
        paths = sorted((root / "approved").glob("*.json"))
        if len(paths) > 1000:
            raise ValueError("memory snapshot exceeds 1000 entries")
        for path in paths:
            item = _entry(path)
            if item["state"] != "approved" or item.get("cleanup_status") != "active":
                continue
            revoked = root / "revoked" / path.name
            if revoked.exists() or revoked.is_symlink():
                read_private(revoked)
                continue
            if item["namespace"] not in namespaces:
                continue
            if mode == "blind" and (item.get("contamination") != "generic" or _FLAG.search(item["body"])):
                raise PermissionError("approved memory is not eligible for blind evaluation")
            if item["namespace"] == "challenge" and item.get("challenge_id") != challenge_id:
                continue
            if not item.get("reviewer") or not item.get("review_basis") or not item.get("license") or not item.get("source_url"):
                raise ValueError("approved memory is missing review/provenance/license")
            self.entries[item["id"]] = item

    def metadata(self) -> dict[str, Any]:
        records = [{key: item.get(key) for key in ("id", "namespace", "body_sha256", "source_url", "license", "contamination")}
                   for item in self.entries.values()]
        return {"mode": self.mode, "namespaces": list(self.namespaces), "versions": records,
                "snapshot_sha256": _hash({"entries": records}), "automatic_writes": False}
