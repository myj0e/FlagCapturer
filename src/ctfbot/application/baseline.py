"""Stage A baseline wiring for a formally admitted offline pilot."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from ctfbot.agent.loop import AgentLoop, RunLimits, RunResult
from ctfbot.application.control import RunControl
from ctfbot.application.lifecycle import RunLifecycle
from ctfbot.application.memory import MemoryView
from ctfbot.challenge.local_service import LocalServiceSpec
from ctfbot.challenge.remote import RemoteSpec
from ctfbot.packs.catalog import snapshot as pack_snapshot
from ctfbot.evidence.store import EvidenceStore
from ctfbot.evidence.store import require_private_regular_file
from ctfbot.evidence.redaction import redact_sensitive_text
from ctfbot.model_adapters.protocol import ModelSession
from ctfbot.runtime.docker import DockerRuntime, DockerLimits
from ctfbot.tools.registry import ToolRegistry


class BaselineAdmissionError(ValueError):
    pass


def validate_baseline_snapshot(workspace_path: Path, oracle_path: Path | None, runs_root: Path) -> tuple[Path, Path | None, dict[str, Any], str]:
    """Fail closed on unadmitted data before opening a provider or runtime."""
    workspace = _private_input(workspace_path)
    oracle = _private_input(oracle_path) if oracle_path is not None else None
    if not workspace.is_dir():
        raise BaselineAdmissionError("expected an imported workspace directory; use TUI Preview to import a single challenge file")
    required = {"TASK.md", "provenance.json", "input"}
    if not required.issubset({entry.name for entry in workspace.iterdir()}):
        raise BaselineAdmissionError("workspace is not an importer-generated challenge snapshot")
    if not workspace.is_dir() or not (workspace / "input").is_dir() or not (workspace / "TASK.md").is_file():
        raise BaselineAdmissionError("challenge workspace is incomplete")
    if stat.S_IMODE(workspace.stat().st_mode) & 0o077:
        raise BaselineAdmissionError("challenge workspace must remain private to its owner")
    if stat.S_IMODE((workspace / "input").stat().st_mode) & 0o222:
        raise BaselineAdmissionError("challenge input directory must be read-only")
    if {entry.name for entry in workspace.iterdir()} != required:
        raise BaselineAdmissionError("challenge workspace contains unexpected top-level files")
    if any((workspace / name).is_symlink() for name in ("TASK.md", "provenance.json", "input")):
        raise BaselineAdmissionError("challenge workspace root contains a symlink")
    if (workspace / "provenance.json").stat().st_size > 256 * 1024:
        raise BaselineAdmissionError("workspace provenance exceeds the 256 KiB limit")
    if any(stat.S_IMODE((workspace / name).stat().st_mode) & 0o222 for name in ("TASK.md", "provenance.json")):
        raise BaselineAdmissionError("TASK.md and provenance must be read-only")
    if oracle is not None and (oracle.is_relative_to(workspace) or oracle.is_relative_to(runs_root.resolve())):
        raise BaselineAdmissionError("oracle must be outside agent workspace and run output")
    try:
        provenance = json.loads((workspace / "provenance.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BaselineAdmissionError("workspace provenance is invalid") from exc
    if not isinstance(provenance, dict):
        raise BaselineAdmissionError("workspace provenance must be a JSON object")
    if provenance.get("formal_admission") != "admitted":
        raise BaselineAdmissionError("pilot challenge has not passed the formal A4 admission review")
    mode = provenance.get("import_mode")
    if mode == "local_service":
        try:
            LocalServiceSpec.from_manifest(provenance.get("local_service"))
        except (ValueError, UnicodeError) as exc:
            raise BaselineAdmissionError(f"invalid local service configuration: {exc}") from exc
    elif mode == "remote":
        try:
            RemoteSpec.from_manifest(provenance.get("remote"))
        except (ValueError, TypeError) as exc:
            raise BaselineAdmissionError(f"invalid remote scope: {exc}") from exc
    elif mode not in {"static_files_only", "offline_artifact_only"}:
        raise BaselineAdmissionError("unsupported challenge import mode")
    elif "local_service" in provenance:
        raise BaselineAdmissionError("local service configuration requires local_service import mode")
    if "remote" in provenance and mode != "remote":
        raise BaselineAdmissionError("remote scope requires remote import mode")
    if "local_service" in provenance and mode != "local_service":
        raise BaselineAdmissionError("local service scope requires local_service import mode")
    if provenance.get("authorization_scope") != "private local evaluation; do not redistribute challenge assets":
        raise BaselineAdmissionError("challenge snapshot is not marked for private local evaluation")
    try:
        if oracle is not None:
            require_private_regular_file(oracle)
            if oracle.stat().st_size > 64 * 1024:
                raise BaselineAdmissionError("private oracle exceeds the 64 KiB limit")
        if (workspace / "TASK.md").stat().st_size > 64 * 1024:
            raise BaselineAdmissionError("TASK.md exceeds the 64 KiB limit")
        task = (workspace / "TASK.md").read_text(encoding="utf-8")
    except BaselineAdmissionError:
        raise
    except (OSError, ValueError, UnicodeError) as exc:
        raise BaselineAdmissionError(f"challenge task or private oracle failed validation: {type(exc).__name__}") from exc
    challenge_id = provenance.get("challenge_id")
    if not isinstance(challenge_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,79}", challenge_id):
        raise BaselineAdmissionError("challenge provenance has an invalid challenge ID")
    if hashlib.sha256(task.encode("utf-8")).hexdigest() != provenance.get("task_sha256"):
        raise BaselineAdmissionError("TASK.md does not match its provenance hash")
    input_records = provenance.get("input_files")
    if not isinstance(input_records, list) or not input_records or len(input_records) > 256:
        raise BaselineAdmissionError("provenance does not list challenge input hashes")
    expected_inputs: dict[str, tuple[int, str]] = {}
    total_input_bytes = 0
    for record in input_records:
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            raise BaselineAdmissionError("provenance contains an invalid input record")
        relative = Path(record["path"])
        if (
            relative.is_absolute()
            or not relative.parts
            or ".." in relative.parts
            or "\\" in record["path"]
            or "\x00" in record["path"]
            or relative.as_posix() != record["path"]
        ):
            raise BaselineAdmissionError("provenance contains an unsafe input path")
        digest = record.get("sha256")
        size = record.get("bytes")
        if (
            not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or not isinstance(size, int)
            or isinstance(size, bool)
            or not 0 <= size <= 32 * 1024 * 1024
        ):
            raise BaselineAdmissionError("provenance contains an invalid input hash or size")
        total_input_bytes += size
        if total_input_bytes > 64 * 1024 * 1024:
            raise BaselineAdmissionError("challenge input exceeds the 64 MiB total limit")
        if relative.as_posix() in expected_inputs:
            raise BaselineAdmissionError("provenance contains duplicate input paths")
        expected_inputs[relative.as_posix()] = (size, digest)
    actual_inputs: dict[str, tuple[int, str]] = {}
    input_root = workspace / "input"
    for path in input_root.rglob("*"):
        if path.is_symlink():
            raise BaselineAdmissionError("challenge workspace contains a symlink")
        if path.is_dir():
            if stat.S_IMODE(path.stat().st_mode) & 0o222:
                raise BaselineAdmissionError("challenge input directories must be read-only")
            continue
        if not path.is_file() or stat.S_IMODE(path.stat().st_mode) & 0o222:
            raise BaselineAdmissionError("challenge input must contain only read-only regular files")
        relative = path.relative_to(input_root).as_posix()
        data = path.read_bytes()
        actual_inputs[relative] = (len(data), hashlib.sha256(data).hexdigest())
    if actual_inputs != expected_inputs:
        raise BaselineAdmissionError("challenge input file set or hashes differ from provenance")
    if oracle is not None:
        try:
            oracle_payload = json.loads(oracle.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BaselineAdmissionError("private oracle is invalid") from exc
        if (
            not isinstance(oracle_payload, dict)
            or oracle_payload.get("challenge_id") != challenge_id
            or oracle_payload.get("source_commit") != provenance.get("source_commit")
            or oracle_payload.get("challenge_metadata_sha256") != provenance.get("challenge_metadata_sha256")
            or not isinstance(oracle_payload.get("flag"), str)
            or not oracle_payload.get("flag")
        ):
            raise BaselineAdmissionError("private oracle provenance does not match the challenge snapshot")
    return workspace, oracle, provenance, task


def _private_input(path: Path) -> Path:
    if path.is_symlink():
        raise BaselineAdmissionError(f"path must not be a symlink: {path}")
    return path.resolve(strict=True)


def run_baseline(
    *,
    workspace: Path,
    oracle_path: Path | None = None,
    runtime_image: str,
    runs_root: Path,
    model: ModelSession | None = None,
    model_metadata: dict[str, Any],
    limits: RunLimits = RunLimits(),
    model_factory: Callable[[], ModelSession] | None = None,
    runtime_factory: Callable[[Path, str], Any] | None = None,
    service_runtime_factory: Callable[[Path, str, LocalServiceSpec, Callable[[dict[str, Any]], None]], Any] | None = None,
    remote_runtime_factory: Callable[[Path, str, RemoteSpec, Callable[[dict[str, Any]], None]], Any] | None = None,
    memory_root: Path | None = None,
    memory_namespaces: tuple[str, ...] = (),
    evaluation_mode: str = "blind",
    control: RunControl | None = None,
    event_sink: Callable[[dict[str, Any]], None] | None = None,
    additional_prompt: str = "",
) -> RunResult:
    baseline_started = time.monotonic()
    workspace, oracle_path, provenance, task = validate_baseline_snapshot(workspace, oracle_path, runs_root)
    if additional_prompt.strip():
        task += "\n\nUser supplemental hints (guidance, not verified facts):\n" + additional_prompt
    local_service = (LocalServiceSpec.from_manifest(provenance["local_service"])
                     if provenance["import_mode"] == "local_service" else None)
    remote = RemoteSpec.from_manifest(provenance["remote"]) if provenance["import_mode"] == "remote" else None
    if remote is not None:
        if remote_runtime_factory is None:
            raise BaselineAdmissionError("remote execution is disabled pending C4 runtime acceptance")
        validate_remote = getattr(remote_runtime_factory, "validate", None)
        if not callable(validate_remote):
            raise BaselineAdmissionError("remote runtime requires a controller grant validator")
        validate_remote(remote, runtime_image, workspace=workspace, runs_root=runs_root)
    if local_service is not None and service_runtime_factory is None:
        raise BaselineAdmissionError("live local services are disabled pending execution-profile isolation acceptance and Docker timeout recovery checks")
    if provenance.get("model_data_authorized") is not True:
        raise BaselineAdmissionError("challenge attachment transmission to a model has not been authorized")
    authorization_basis = provenance.get("model_data_authorization_basis")
    if not isinstance(authorization_basis, str) or not authorization_basis.strip():
        raise BaselineAdmissionError("model data authorization must include a recorded basis")
    if (model is None) == (model_factory is None):
        raise ValueError("provide exactly one of model or model_factory")
    if model is not None and (not hasattr(model, "run_turn") or not hasattr(model, "close")):
        raise TypeError("model factory did not return a model session")
    memory_view = MemoryView(memory_root, namespaces=memory_namespaces, mode=evaluation_mode,
                             challenge_id=provenance["challenge_id"], workspace=workspace, runs_root=runs_root)

    runs_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(runs_root, 0o700)
    run_id = str(uuid.uuid4())
    run_dir = runs_root / run_id
    challenge_id = str(provenance.get("challenge_id", "unknown"))
    evidence = EvidenceStore(
        run_dir,
        event_sink=event_sink,
        run_id=run_id,
        challenge_id=challenge_id,
    )
    lifecycle = RunLifecycle(run_dir, evidence, run_id=run_id, challenge_id=challenge_id)
    controller_work = run_dir / "controller-work"
    controller_work.mkdir(mode=0o700)
    os.chmod(controller_work, 0o700)
    metadata = {
        "result_contract_version": 2,
        "run_id": run_id,
        "provider": model_metadata,
        "challenge_id": provenance.get("challenge_id"),
        "workspace_path": str(workspace),
        "input_root": str(workspace / "input"),
        "input_files": provenance.get("input_files"),
        "import_mode": provenance.get("import_mode"),
        "model_data_authorized": True,
        "model_data_authorization_basis": authorization_basis,
        "provenance_sha256": hashlib.sha256((workspace / "provenance.json").read_bytes()).hexdigest(),
        "recovery": {
            "automatic_retry": False,
            "resume_supported": False,
            "action": "review_partial_evidence_then_start_new_run",
            "parent_run_id": None,
        },
        "source_tree_sha256": _source_tree_hash(Path.cwd()),
        "python": sys.version,
        "platform": platform.platform(),
        "ctfbot_commit": _git_commit(Path.cwd()),
        "runtime_image": runtime_image,
        "verification": {"method": "exact-string controller-only" if oracle_path else "model-selected candidate; correctness unverified"},
        "local_service": provenance.get("local_service"),
        "remote": provenance.get("remote"),
        "domain_packs": pack_snapshot(),
        "memory": memory_view.metadata(),
        "limits": {
            "max_turns": limits.max_turns,
            "max_tool_calls": limits.max_tool_calls,
            "wall_time_seconds": limits.wall_time_seconds,
            "tool_timeout_seconds": limits.tool_timeout_seconds,
            "per_tool_output_bytes": limits.per_tool_output_bytes,
            "total_model_output_bytes": limits.total_model_output_bytes,
        },
    }
    meta_path = run_dir / "run.json"
    _write_private_json(meta_path, metadata)
    evidence.append("run_metadata", **metadata)

    challenge_input = workspace / "input"

    def record_service_event(details: dict[str, Any]) -> None:
        payload = dict(details)
        log_bytes = payload.pop("log_bytes", None)
        if isinstance(log_bytes, bytes):
            payload["log_evidence"] = evidence.write_artifact(log_bytes, media_type="text/plain; charset=utf-8")
        evidence.append("service_lifecycle", **payload)

    def record_remote_event(details: dict[str, Any]) -> None:
        payload = dict(details)
        for key in ("request_bytes", "response_bytes"):
            data = payload.pop(key, None)
            if isinstance(data, bytes):
                payload[key.replace("_bytes", "_evidence")] = evidence.write_artifact(data, media_type="application/octet-stream")
        evidence.append("remote_lifecycle", **payload)

    runtime = None
    loop: AgentLoop | None = None
    run_result: RunResult | None = None
    model_created_by_factory = model is None
    phase = "provider_initialization" if model_created_by_factory else "runtime_initialization"
    try:
        if control is not None and control.cancelled:
            evidence.append("run_cancelled", reason="user_request", phase="preparation")
            cleanup_status = "complete" if _close_model(model, evidence) else "error"
            run_result = _empty_result(
                run_id, run_dir, "user_cancelled", "user_cancelled", loop=None
            )
            evidence.append(
                "run_finished",
                status=run_result.status,
                stop_reason=run_result.stop_reason,
                turns=0,
                tool_calls=0,
                elapsed_seconds=round(time.monotonic() - baseline_started, 3),
            )
            lifecycle.transition(
                "cancelled", phase="preparation", result_status=run_result.status,
                stop_reason=run_result.stop_reason,
                cleanup_status=cleanup_status,
            )
            return run_result

        lifecycle.transition("starting", phase=phase)
        if model_created_by_factory and local_service is None and remote is None:
            assert model_factory is not None
            model = model_factory()
            if not hasattr(model, "run_turn") or not hasattr(model, "close"):
                raise TypeError("model factory did not return a model session")
            metadata["provider"] = dict(model_metadata)
            _write_private_json(meta_path, metadata)
            evidence.append("provider_initialized", provider=dict(model_metadata))
        phase = "runtime_startup"
        lifecycle.transition("starting", phase=phase)
        if remote is not None:
            assert remote_runtime_factory is not None
            runtime = remote_runtime_factory(challenge_input, runtime_image, remote, record_remote_event)
            runtime.set_startup_deadline(baseline_started + limits.wall_time_seconds)
        elif local_service is not None:
            assert service_runtime_factory is not None
            runtime = service_runtime_factory(
                challenge_input, runtime_image, local_service,
                record_service_event,
            )
            set_deadline = getattr(runtime, "set_startup_deadline", None)
            if callable(set_deadline):
                set_deadline(baseline_started + limits.wall_time_seconds)
        else:
            runtime = runtime_factory(challenge_input, runtime_image) if runtime_factory else DockerRuntime(
                challenge_input,
                runtime_image,
                limits=DockerLimits(
                    cpus=2,
                    memory="4g",
                    memory_swap="4g",
                    pids=256,
                    work_tmpfs="2g",
                ),
            )
        if control is not None:
            cancel_model = getattr(model, "cancel", None)
            if callable(cancel_model):
                control.add_cancel_callback(cancel_model)
            cancel_runtime = getattr(runtime, "cancel", None)
            if callable(cancel_runtime):
                control.add_cancel_callback(cancel_runtime)
        with runtime:
            if control is not None and control.cancelled:
                raise RuntimeError("run cancelled during runtime preparation")
            if time.monotonic() - baseline_started >= limits.wall_time_seconds:
                raise TimeoutError("runtime startup exhausted the run wall-time budget")
            if model is None:
                phase = "provider_initialization"
                lifecycle.transition("starting", phase=phase)
                assert model_factory is not None
                model = model_factory()
                if not hasattr(model, "run_turn") or not hasattr(model, "close"):
                    raise TypeError("model factory did not return a model session")
                metadata["provider"] = dict(model_metadata)
                _write_private_json(meta_path, metadata)
                evidence.append("provider_initialized", provider=dict(model_metadata))
                if control is not None:
                    cancel_model = getattr(model, "cancel", None)
                    if callable(cancel_model):
                        control.add_cancel_callback(cancel_model)
            elapsed_startup = time.monotonic() - baseline_started
            remaining_wall_time = limits.wall_time_seconds - elapsed_startup
            if remaining_wall_time <= 0:
                raise TimeoutError("runtime startup exhausted the run wall-time budget")
            registry = ToolRegistry(
                challenge_input,
                controller_work,
                evidence,
                runtime,
                oracle_path,
                command_timeout=limits.tool_timeout_seconds,
                max_model_output_bytes=limits.per_tool_output_bytes,
                service_endpoint=local_service.endpoint if local_service else None,
                memory_view=memory_view,
            )
            loop = AgentLoop(
                model,
                registry,
                evidence,
                limits=replace(limits, wall_time_seconds=remaining_wall_time),
                challenge_id=challenge_id,
                challenge_provenance=provenance,
                control=control,
                run_id=run_id,
            )
            phase = "agent_execution"
            lifecycle.transition("running", phase=phase)
            run_result = loop.run(task)
            lifecycle.transition(
                "stopping", phase="runtime_teardown", result_status=run_result.status,
                stop_reason=run_result.stop_reason,
            )
        assert run_result is not None
        lifecycle.transition(
            _lifecycle_terminal_state(run_result),
            phase="finished",
            result_status=run_result.status,
            stop_reason=run_result.stop_reason,
            cleanup_status="error" if run_result.cleanup_errors else "complete",
        )
        return run_result
    except Exception as exc:
        if run_result is not None:
            evidence.append(
                "run_cleanup_error",
                phase="runtime_context_exit",
                error_type=type(exc).__name__,
                message=redact_sensitive_text(str(exc)),
            )
            cleanup_status = "error"
            close_runtime = getattr(runtime, "close", None)
            if callable(close_runtime):
                try:
                    close_runtime()
                    cleanup_status = "recovered"
                except Exception as close_exc:
                    evidence.append(
                        "runtime_close_error",
                        kind=type(close_exc).__name__,
                        message=redact_sensitive_text(str(close_exc)),
                    )
            if run_result.cleanup_errors:
                cleanup_status = "error"
            lifecycle.transition(
                _lifecycle_terminal_state(run_result),
                phase="finished_with_cleanup_error",
                result_status=run_result.status,
                stop_reason=run_result.stop_reason,
                cleanup_status=cleanup_status,
            )
            return run_result
        if control is not None and control.cancelled:
            cleanup_status = "complete"
            close_runtime = getattr(runtime, "close", None)
            if runtime is not None and not callable(close_runtime):
                cleanup_status = "unknown"
            elif callable(close_runtime):
                try:
                    close_runtime()
                except Exception as close_exc:
                    cleanup_status = "error"
                    evidence.append(
                        "runtime_close_error",
                        kind=type(close_exc).__name__,
                        message=redact_sensitive_text(str(close_exc)),
                    )
            turns, tool_calls = _loop_counts(loop)
            evidence.append("run_cancelled", reason="user_request", phase=phase)
            model_closed_by_loop = bool(loop and loop.model_close_attempted)
            if not model_closed_by_loop and not _close_model(model, evidence):
                cleanup_status = "error"
            elif model_closed_by_loop and loop and loop.cleanup_errors:
                cleanup_status = "error"
            run_result = _empty_result(
                run_id, run_dir, "user_cancelled", "user_cancelled", loop=loop
            )
            evidence.append(
                "run_finished",
                status=run_result.status,
                stop_reason=run_result.stop_reason,
                turns=turns,
                tool_calls=tool_calls,
                elapsed_seconds=round(time.monotonic() - baseline_started, 3),
            )
            lifecycle.transition(
                "cancelled", phase="finished", result_status=run_result.status,
                stop_reason=run_result.stop_reason,
                cleanup_status=cleanup_status,
            )
            return run_result
        turns, tool_calls = _loop_counts(loop)
        failure_phase = phase
        if phase == "provider_initialization":
            status = "provider_error"
            stop_reason = "provider_initialization_error"
            failure_class = "provider_error"
            lifecycle_state = "failed"
        elif isinstance(exc, TimeoutError):
            status = "budget_exhausted"
            stop_reason = "wall_time_exhausted"
            failure_class = "timeout"
            lifecycle_state = "timed_out"
        else:
            status = "error"
            stop_reason = "runtime_or_controller_error"
            failure_class = "environment_error" if phase == "runtime_startup" else "controller_error"
            lifecycle_state = "failed"
        evidence.append(
            "run_failed",
            failure_class=failure_class,
            phase=failure_phase,
            error_type=type(exc).__name__,
            message=redact_sensitive_text(str(exc)),
            turns=turns,
            tool_calls=tool_calls,
        )
        cleanup_status = "complete"
        close_runtime = getattr(runtime, "close", None)
        if runtime is not None and not callable(close_runtime):
            cleanup_status = "unknown"
        elif callable(close_runtime):
            try:
                close_runtime()
            except Exception as close_exc:
                cleanup_status = "error"
                evidence.append(
                    "runtime_close_error",
                    kind=type(close_exc).__name__,
                    message=redact_sensitive_text(str(close_exc)),
                )
        model_closed_by_loop = bool(loop and loop.model_close_attempted)
        if not model_closed_by_loop and not _close_model(model, evidence):
            cleanup_status = "error"
        elif model_closed_by_loop and loop and loop.cleanup_errors:
            cleanup_status = "error"
        evidence.append(
            "run_finished",
            status=status,
            stop_reason=stop_reason,
            turns=turns,
            tool_calls=tool_calls,
            elapsed_seconds=round(time.monotonic() - baseline_started, 3),
        )
        run_result = RunResult(
            run_id=run_id,
            status=status,
            stop_reason=stop_reason,
            turns=turns,
            tool_calls=tool_calls,
            verified=bool(loop and loop.verified),
            run_dir=str(run_dir),
            usage_totals=dict(loop.usage_totals) if loop else {},
            cleanup_errors=tuple(loop.cleanup_errors) if loop else (),
        )
        lifecycle.transition(
            lifecycle_state,
            phase="finished",
            result_status=status,
            stop_reason=stop_reason,
            failure_class=failure_class,
            cleanup_status=cleanup_status,
        )
        return run_result
    finally:
        if control is not None:
            control.wait_for_callbacks()


def _write_private_json(path: Path, value: dict[str, Any]) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _loop_counts(loop: AgentLoop | None) -> tuple[int, int]:
    return (loop.turns, loop.tool_calls) if loop else (0, 0)


def _empty_result(run_id: str, run_dir: Path, status: str, stop_reason: str,
                  *, loop: AgentLoop | None) -> RunResult:
    turns, tool_calls = _loop_counts(loop)
    return RunResult(
        run_id=run_id,
        status=status,
        stop_reason=stop_reason,
        turns=turns,
        tool_calls=tool_calls,
        verified=bool(loop and loop.verified),
        run_dir=str(run_dir),
        usage_totals=dict(loop.usage_totals) if loop else {},
        cleanup_errors=tuple(loop.cleanup_errors) if loop else (),
    )


def _close_model(model: ModelSession | None, evidence: EvidenceStore) -> bool:
    if model is None:
        return True
    try:
        model.close()
    except Exception as close_exc:
        evidence.append(
            "provider_close_error",
            kind=type(close_exc).__name__,
            message=redact_sensitive_text(str(close_exc)),
        )
        return False
    return True


def _lifecycle_terminal_state(result: RunResult) -> str:
    if result.status == "user_cancelled":
        return "cancelled"
    if result.stop_reason in {"provider_timeout", "wall_time_exhausted"}:
        return "timed_out"
    if result.status in {"error", "provider_error"}:
        return "failed"
    return "completed"


def _git_commit(root: Path) -> str | None:
    try:
        result = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                                capture_output=True, timeout=2, check=False, text=True)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _source_tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    source = root / "src" / "ctfbot"
    for path in sorted(source.rglob("*.py")):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        data = path.read_bytes()
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    project_metadata = root / "pyproject.toml"
    if project_metadata.is_file():
        digest.update(project_metadata.read_bytes())
    return digest.hexdigest()
