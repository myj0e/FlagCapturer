"""Evidence integrity audit and explicit offline command replay without a model."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from ctfbot.application.baseline import validate_baseline_snapshot
from ctfbot.evidence.store import EvidenceStore, require_private_regular_file
from ctfbot.runtime.service_recovery import daemon_identity, private_directory, read_private, write_private
from ctfbot.runtime.supervised import SupervisedOfflineRuntime


def _events(root: Path) -> list[dict[str, Any]]:
    path = require_private_regular_file(root / "events.jsonl")
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("evidence log exceeds 64 MiB audit cap")
    events = []
    for expected, line in enumerate(path.read_text().splitlines(), start=1):
        record = json.loads(line)
        if not isinstance(record, dict) or type(record.get("seq")) is not int or record.get("seq") != expected:
            raise ValueError("evidence event sequence is incomplete or reordered")
        events.append(record)
    return events


def _artifact(root: Path, ref: dict[str, Any]) -> bytes:
    identity = ref.get("sha256")
    if not isinstance(identity, str) or len(identity) != 64 or any(c not in "0123456789abcdef" for c in identity):
        raise ValueError("artifact reference has an invalid SHA-256")
    relative = f"artifacts/sha256-{identity}.bin"
    if ref.get("artifact") != relative:
        raise ValueError("artifact reference is not content-addressed")
    if (root / "artifacts").is_symlink():
        raise ValueError("artifact directory must not be a symlink")
    path = require_private_regular_file(root / relative)
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("artifact exceeds replay read cap")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != identity or len(data) != ref.get("bytes"):
        raise ValueError("artifact hash or byte count differs from evidence")
    return data


def audit_run(run_dir: Path) -> dict[str, Any]:
    if run_dir.is_symlink():
        raise ValueError("run directory cannot be a symlink")
    root = run_dir.resolve(strict=True)
    metadata = read_private(root / "run.json")
    events = _events(root)
    for event in events:
        for key in ("run_id", "challenge_id"):
            if key in event and event[key] != metadata.get(key):
                raise ValueError("evidence contains an event from a different run/challenge")
    checked = set()
    def visit(value):
        if isinstance(value, dict):
            if isinstance(value.get("artifact"), str):
                _artifact(root, value)
                checked.add(value["artifact"])
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(events)
    commands = [event for event in events if event.get("event_type") == "command_execution"]
    return {"schema_version": 1, "run_id": metadata.get("run_id"), "integrity": "passed",
            "checked_artifacts": len(checked), "events": len(events), "command_checkpoints": len(commands),
            "terminal_event_present": any(event.get("event_type") == "run_finished" for event in events),
            "claim": "artifact integrity and event ordering only; evidence is not digitally signed"}


def replay_run(*, run_dir: Path, workspace: Path, oracle: Path | None = None, output: Path,
               maximum_commands: int = 60, wall_seconds: float = 300,
               runtime_factory: Callable[[Path, str], Any] | None = None) -> Path:
    if not 1 <= maximum_commands <= 60 or not 0 < wall_seconds <= 1800:
        raise ValueError("replay requires at most 60 commands and 1800 seconds")
    audit = audit_run(run_dir)
    root = run_dir.resolve(strict=True)
    metadata = read_private(root / "run.json")
    if metadata.get("import_mode") not in {"static_files_only", "offline_artifact_only"}:
        raise PermissionError("this replay increment accepts offline runs only; no remote/service scope is inferred")
    admitted, _, _, _ = validate_baseline_snapshot(workspace, oracle, output)
    if (str(admitted) != metadata.get("workspace_path")
            or hashlib.sha256((admitted / "provenance.json").read_bytes()).hexdigest() != metadata.get("provenance_sha256")):
        raise ValueError("replay workspace differs from the original admitted snapshot")
    if output.resolve().is_relative_to(admitted) or admitted.is_relative_to(output.resolve()):
        raise ValueError("replay output and challenge workspace must be disjoint")
    events = _events(root)
    commands = [event for event in events if event.get("event_type") == "command_execution"]
    if not commands or len(commands) > maximum_commands:
        raise ValueError("recorded command checkpoints are empty or exceed replay allocation")
    # Validate all argv before starting Docker. No model or oracle contents are used.
    decoded = []
    for event in commands:
        argv = json.loads(_artifact(root, event["argv"]))
        if (not isinstance(argv, list) or not 1 <= len(argv) <= 64
                or any(not isinstance(value, str) or "\x00" in value for value in argv)
                or sum(len(value.encode()) for value in argv) > 16384):
            raise ValueError("recorded argv is not within command policy")
        decoded.append((event, argv))
    private_directory(output)
    replay_id = str(uuid.uuid4())
    evidence = EvidenceStore(output / f"replay-{replay_id}", run_id=replay_id, challenge_id=metadata["challenge_id"])
    deadline = time.monotonic() + wall_seconds
    runtime = None
    checkpoints = []
    status = "running"
    summary = evidence.run_dir / "replay.json"
    def persist():
        write_private(summary, {"schema_version": 1, "origin_run_id": metadata["run_id"], "status": status,
                      "origin_audit": audit, "runtime_image": metadata["runtime_image"], "checkpoints": checkpoints,
                      "matched_over_replayed": [sum(item["matched"] for item in checkpoints), len(checkpoints)],
                      "skipped_interactive_or_network_tools": any(event.get("event_type") == "tool_call" and
                          (str(event.get("name", "")).startswith("session_") or event.get("name") in {"remote_tcp_exchange", "http_request"}) for event in events),
                      "verification": "not replayed; command output hashes do not independently verify a flag",
                      "scope": "offline command subset; no model, remote connection, memory search or flag submission"})
    persist()
    try:
        runtime = (runtime_factory(admitted / "input", metadata["runtime_image"]) if runtime_factory else
                   SupervisedOfflineRuntime(admitted / "input", metadata["runtime_image"],
                                            recovery_root=output / "replay-state", expected_daemon=daemon_identity(),
                                            event_sink=lambda details: evidence.append("replay_runtime", **details)))
        if hasattr(runtime, "startup_timeout"):
            runtime.startup_timeout = min(runtime.startup_timeout, max(0.1, deadline - time.monotonic()))
        with runtime:
            for event, argv in decoded:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    status = "budget_exhausted"
                    break
                result = runtime.execute(argv, timeout=min(120, remaining, event["timeout_seconds"]))
                stdout = evidence.write_artifact(result.stdout)
                stderr = evidence.write_artifact(result.stderr)
                matched = (result.exit_code == event["exit_code"] and result.timed_out == event["timed_out"]
                           and result.truncated == event["truncated"] and stdout["sha256"] == event["stdout_evidence"]["sha256"]
                           and stderr["sha256"] == event["stderr_evidence"]["sha256"])
                checkpoints.append({"origin_seq": event["seq"], "matched": matched, "stdout": stdout,
                                    "stderr": stderr, "exit_code": result.exit_code})
                persist()
                if result.timed_out:
                    status = "command_timeout"; break
            else:
                status = "matched" if all(item["matched"] for item in checkpoints) else "diverged"
    except (Exception, KeyboardInterrupt) as exc:
        status = "user_cancelled" if isinstance(exc, KeyboardInterrupt) else "runtime_or_cleanup_error"
        evidence.append("replay_error", error_type=type(exc).__name__)
        cancel = getattr(runtime, "cancel", None)
        if isinstance(exc, KeyboardInterrupt) and callable(cancel):
            try:
                cancel()
            except Exception:
                evidence.append("replay_cancel_incomplete")
        close = getattr(runtime, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                evidence.append("replay_cleanup_incomplete")
    persist()
    return summary
