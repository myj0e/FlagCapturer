"""Private append-only run events and content-addressed evidence artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import re
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class EvidenceIntegrityError(ValueError):
    pass


class EvidenceStore:
    def __init__(self, run_dir: Path, *, event_sink: Callable[[dict[str, Any]], None] | None = None,
                 run_id: str | None = None, challenge_id: str | None = None) -> None:
        self.run_dir = run_dir.absolute()
        self.artifact_dir = self.run_dir / "artifacts"
        self.events_path = self.run_dir / "events.jsonl"
        self.run_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
        os.chmod(self.run_dir, 0o700)
        self.artifact_dir.mkdir(mode=0o700)
        os.chmod(self.artifact_dir, 0o700)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(self.events_path, flags, 0o600)
        os.close(fd)
        self._sequence = 0
        self._artifacts: dict[str, dict[str, Any]] = {}
        self._event_sink = event_sink
        self._lock = threading.RLock()
        self._context = {
            key: value for key, value in (("run_id", run_id), ("challenge_id", challenge_id))
            if value is not None
        }

    def write_artifact(self, data: bytes, *, media_type: str = "application/octet-stream") -> dict[str, Any]:
        digest = hashlib.sha256(data).hexdigest()
        name = f"sha256-{digest}.bin"
        target = self.artifact_dir / name
        with self._lock:
            if not target.exists():
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
        ref = {
            "artifact": f"artifacts/{name}",
            "sha256": digest,
            "bytes": len(data),
            "media_type": media_type,
        }
        self._artifacts[ref["artifact"]] = ref
        return ref

    def read_artifact(self, relative: str, *, maximum: int = 32 * 1024 * 1024) -> tuple[bytes, dict[str, Any]]:
        """Read only an artifact produced by this store, checking its identity."""
        if not isinstance(relative, str) or not re.fullmatch(r"artifacts/sha256-[0-9a-f]{64}\.bin", relative):
            raise ValueError("invalid artifact reference")
        ref = self._artifacts.get(relative)
        if ref is None:
            raise PermissionError("artifact is not owned by this run")
        if self.artifact_dir.is_symlink():
            raise PermissionError("artifact directory must not be a symlink")
        fd = os.open(self.run_dir / relative, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077 or info.st_size > maximum:
                raise ValueError("artifact is not a bounded private regular file")
            data = stream.read(maximum+1)
        if len(data) != ref["bytes"] or hashlib.sha256(data).hexdigest() != ref["sha256"]:
            raise EvidenceIntegrityError("artifact hash/length differs from this run's recorded source")
        return data, dict(ref)

    def reference_status(self, relative: str) -> str:
        """Cheap index metadata; actual reads still verify the complete hash."""
        if relative not in self._artifacts:
            return "not_owned_by_run"
        target = self.run_dir / relative
        if not target.exists():
            return "missing"
        if target.is_symlink() or self.artifact_dir.is_symlink():
            return "unsafe_path"
        return "present_hash_unchecked"

    def append(self, event_type: str, **fields: Any) -> dict[str, Any]:
        with self._lock:
            self._sequence += 1
            record = {
                "seq": self._sequence,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "event_type": event_type,
                **self._context,
                **fields,
            }
            encoded = (json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode(
                "utf-8"
            )
            fd = os.open(self.events_path, os.O_WRONLY | os.O_APPEND)
            with os.fdopen(fd, "ab") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            if self._event_sink is not None:
                try:
                    self._event_sink(record)
                except Exception:
                    # A presentation sink must not change the persisted run outcome.
                    pass
        return record


def require_private_regular_file(path: Path) -> Path:
    if path.is_symlink():
        raise ValueError("private file must not be a symlink")
    resolved = path.resolve(strict=True)
    info = resolved.stat()
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError("private file must be a regular file with mode 0600 or stricter")
    if stat.S_IMODE(resolved.parent.stat().st_mode) & 0o077:
        raise ValueError("private file parent directory must have mode 0700 or stricter")
    return resolved
