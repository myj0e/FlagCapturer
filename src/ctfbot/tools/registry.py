"""Small policy-checked tool registry for the Stage A single-agent loop."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from ctfbot.evidence.store import EvidenceStore, EvidenceIntegrityError
from ctfbot.model_adapters.protocol import ToolCall, ToolReply, ToolSpec
from ctfbot.runtime.sessions import InteractiveReadResult
from ctfbot.verification.exact import verify_exact


class CommandRuntime(Protocol):
    def execute(self, argv: list[str], *, timeout: float) -> "CommandResult": ...


class InteractiveRuntime(CommandRuntime, Protocol):
    def start_interactive(self, session_id: str, argv: list[str], *, on_output: Callable[[bytes], None],
                          on_closed: Callable[[str, int | None], None]) -> None: ...
    def send_interactive(self, session_id: str, data: bytes) -> None: ...
    def read_interactive(self, session_id: str, *, timeout: float, maximum_bytes: int) -> InteractiveReadResult: ...
    def close_interactive(self, session_id: str) -> InteractiveReadResult: ...


@dataclass(frozen=True, slots=True)
class CommandResult:
    exit_code: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    reply: ToolReply
    result: dict[str, Any]


Handler = Callable[[Mapping[str, Any]], ToolOutcome]


def _schema_string(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description, "maxLength": 4096}


class ToolRegistry:
    def __init__(self, challenge_root: Path, work_root: Path, evidence: EvidenceStore, runtime: CommandRuntime | None,
                 oracle_path: Path | None, *, command_timeout: float = 120,
                 max_file_read_bytes: int = 64 * 1024, max_model_output_bytes: int = 16 * 1024,
                 service_endpoint: str | None = None, memory_view: Any | None = None) -> None:
        self.challenge_root = challenge_root.resolve(strict=True)
        self.work_root = work_root.resolve(strict=True)
        if not self.challenge_root.is_dir() or not self.work_root.is_dir():
            raise ValueError("challenge and work roots must be directories")
        if self.challenge_root == self.work_root or self.challenge_root.is_relative_to(self.work_root) or self.work_root.is_relative_to(self.challenge_root):
            raise ValueError("challenge and work roots must be disjoint")
        self.evidence = evidence
        self.memory_view = memory_view
        self.runtime = runtime
        self.oracle_path = oracle_path.resolve(strict=True) if oracle_path else None
        if self.oracle_path and (self.oracle_path.is_relative_to(self.challenge_root) or self.oracle_path.is_relative_to(self.work_root)):
            raise ValueError("oracle must remain outside the challenge and work roots")
        self.command_timeout = command_timeout
        self.max_file_read_bytes = max_file_read_bytes
        if max_model_output_bytes < 256:
            raise ValueError("tool output limit must be at least 256 bytes")
        self.max_model_output_bytes = max_model_output_bytes
        if service_endpoint is not None:
            match = re.fullmatch(r"challenge:([0-9]{4,5})", service_endpoint)
            if match is None or not 1024 <= int(match.group(1)) <= 65535:
                raise ValueError("local service scope must name the approved challenge TCP port")
        self.service_endpoint = service_endpoint
        self.network_description = (f"Only local TCP endpoint {service_endpoint} is authorized."
                                    if service_endpoint else "Network is disabled.")
        remote_spec = getattr(runtime, "remote_spec", None)
        if remote_spec is not None:
            self.network_description = (f"The command container has no network. Only remote_tcp_exchange can reach "
                                        f"{remote_spec.endpoint}, within the controller authorization time window.")
        self._session_transcripts: dict[str, _SessionTranscript] = {}
        self._session_lock = threading.RLock()
        self._definitions: dict[str, tuple[ToolSpec, Handler]] = {
            "challenge_list": (
                ToolSpec("challenge_list", "List entries in the read-only challenge workspace.", {
                    "type": "object", "properties": {"path": _schema_string("Relative path; use '.' for the root.")},
                    "required": ["path"], "additionalProperties": False,
                }), self._list_challenge),
            "challenge_read_text": (
                ToolSpec("challenge_read_text", "Read a bounded UTF-8 text file from the read-only challenge workspace.", {
                    "type": "object", "properties": {"path": _schema_string("Relative file path.")},
                    "required": ["path"], "additionalProperties": False,
                }), self._read_challenge_text),
            "command_run": (
                ToolSpec("command_run", f"Run one argv command in the isolated /work directory. {self.network_description} No shell is implied.", {
                    "type": "object", "properties": {"argv": {
                        "type": "array", "items": {"type": "string", "maxLength": 4096},
                        "minItems": 1, "maxItems": 64,
                    }}, "required": ["argv"], "additionalProperties": False,
                }), self._run_command),
            "candidate_submit": (
                ToolSpec("candidate_submit", "Submit a flag candidate. An optional known-answer oracle can verify correctness; without it, any model-selected candidate is recorded as unverified with no format check. Oracle contents are never returned.", {
                    "type": "object", "properties": {"candidate": {"type": "string", "maxLength": 4096}},
                    "required": ["candidate"], "additionalProperties": False,
                }), self._submit_candidate),
        }
        if runtime is not None and all(callable(getattr(runtime, name, None)) for name in (
            "start_interactive", "send_interactive", "read_interactive", "close_interactive",
        )):
            self._definitions.update(self._interactive_definitions())
        if remote_spec is not None and callable(getattr(runtime, "remote_exchange", None)):
            self._definitions["remote_tcp_exchange"] = (
                ToolSpec("remote_tcp_exchange", "Send bounded base64 bytes over one authorized TCP connection; half-close input, read until EOF, cap or timeout. No DNS, HTTP redirect or proxy processing. A fresh connection is used for each call.", {
                    "type": "object", "properties": {
                        "host": _schema_string("Exact authorized host."),
                        "port": {"type": "integer", "minimum": 1, "maximum": 65535},
                        "protocol": {"type": "string", "enum": ["tcp"]},
                        "data_base64": {"type": "string", "maxLength": 21848},
                    }, "required": ["host", "port", "protocol", "data_base64"], "additionalProperties": False,
                }), self._remote_exchange)
        from ctfbot.tools.domain import install_domain_tools
        install_domain_tools(self)
        from ctfbot.tools.reliability import ReliabilityTools
        self.reliability = ReliabilityTools(self)

    def _remote_exchange(self, arguments: Mapping[str, Any]) -> ToolOutcome:
        host, port, protocol = arguments["host"], arguments["port"], arguments["protocol"]
        if not isinstance(host, str) or type(port) is not int or protocol != "tcp":
            raise ValueError("remote target requires a host, integer port and tcp protocol")
        encoded = arguments["data_base64"]
        if not isinstance(encoded, str) or len(encoded) > 21848:
            raise ValueError("remote request exceeds the 16 KiB input limit")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("remote request must use valid base64") from exc
        receive_limit = min(16384, max(1, self.max_model_output_bytes // 4))
        result = self.runtime.remote_exchange(host, port, protocol, data,
                                             timeout=min(self.command_timeout, 30),
                                             maximum_bytes=receive_limit)
        request = self.evidence.write_artifact(data, media_type="application/octet-stream")
        response = self.evidence.write_artifact(result["data"], media_type="application/octet-stream")
        payload = {"status": result["status"], "sent_bytes": result["sent_bytes"],
                   "data_base64": base64.b64encode(result["data"]).decode("ascii"),
                   "request_evidence": request, "response_evidence": response,
                   "coverage": {"source_kind": "controlled_request", "received_bytes": len(result['data']),
                                "receive_limit_bytes": receive_limit, "filter": "single granted TCP connection; bounded response",
                                "truncated": len(result['data']) >= receive_limit, "transport_status": result['status']}}
        # Artifact metadata may be longer than a very small model output budget.
        content = json.dumps(payload, ensure_ascii=True)
        if len(content.encode()) > self.max_model_output_bytes:
            content = json.dumps({"status": result["status"], "response_artifact": response["artifact"]})
        return ToolOutcome(ToolReply(content, result["status"] != "timed_out"),
                           {key: value for key, value in payload.items() if key != "data_base64"})

    @property
    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(spec for spec, _ in self._definitions.values())

    def invoke(self, call: ToolCall) -> ToolOutcome:
        outcome = self._invoke(call)
        return self.reliability.observe(call, outcome)

    def _invoke(self, call: ToolCall) -> ToolOutcome:
        definition = self._definitions.get(call.name)
        if definition is None:
            return ToolOutcome(ToolReply(f"Tool is not registered: {call.name}", False), {"status": "policy_denied"})
        if not isinstance(call.arguments, Mapping):
            return ToolOutcome(ToolReply("Tool arguments must be a JSON object.", False), {"status": "invalid_arguments"})
        spec, handler = definition
        allowed = set(spec.input_schema.get("properties", {}))
        if set(call.arguments) - allowed or any(key not in call.arguments for key in spec.input_schema.get("required", [])):
            return ToolOutcome(ToolReply(f"Arguments do not match the schema for {call.name}.", False), {"status": "invalid_arguments"})
        try:
            from ctfbot.tools.schema import validate
            validate(dict(call.arguments), spec.input_schema)
        except ValueError as exc:
            return ToolOutcome(ToolReply(f"Invalid arguments: {exc}", False), {
                "status": "invalid_arguments", "error_kind": "invalid_arguments"})
        try:
            return handler(call.arguments)
        except PermissionError as exc:
            return ToolOutcome(ToolReply(f"Access denied: {exc}", False), {"status": "policy_denied"})
        except (OSError, UnicodeError, ValueError, TypeError, RuntimeError) as exc:
            kind = ("source_mismatch" if isinstance(exc, EvidenceIntegrityError) else
                    "input_format_mismatch" if isinstance(exc, UnicodeError) else
                    "input_path_error" if isinstance(exc, FileNotFoundError) else
                    "invalid_arguments" if isinstance(exc, (ValueError, TypeError)) else "runtime_error")
            error = self.evidence.write_artifact(str(exc).encode(), media_type="text/plain; charset=utf-8")
            return ToolOutcome(ToolReply(f"Tool error: {type(exc).__name__}: {exc}", False), {
                "status": "tool_error", "error_kind": kind, "error_evidence": error,
                "next_step": "Check input magic/type and paths; use focused evidence reads. Do not infer missing packages from an API or parsing error."})

    @staticmethod
    def _checked_file(root: Path, relative: Any, *, must_exist: bool = True) -> Path:
        if not isinstance(relative, str) or "\x00" in relative or "\\" in relative:
            raise ValueError("path must be a relative POSIX path")
        if len(relative) > 4096:
            raise ValueError("path exceeds the 4096-character limit")
        candidate = PurePosixPath(relative)
        if candidate.is_absolute() or any(part in {"..", "."} for part in candidate.parts if relative != "."):
            raise PermissionError("path must stay inside the challenge workspace")
        if relative in {"", "."}:
            target = root
        else:
            target = root.joinpath(*candidate.parts)
        cursor = root
        for part in candidate.parts if relative != "." else ():
            cursor = cursor / part
            if cursor.is_symlink():
                raise PermissionError("symlink traversal is not allowed for challenge files")
        try:
            resolved = target.resolve(strict=must_exist)
        except FileNotFoundError:
            raise
        if not resolved.is_relative_to(root):
            raise PermissionError("resolved path escapes the challenge workspace")
        return resolved

    def _list_challenge(self, arguments: Mapping[str, Any]) -> ToolOutcome:
        target = self._checked_file(self.challenge_root, arguments["path"])
        if not target.is_dir():
            raise ValueError("path is not a directory")
        entries = []
        for child in sorted(target.iterdir(), key=lambda item: item.name)[:1000]:
            if child.is_symlink():
                continue
            entry = {"name": child.name, "kind": "directory" if child.is_dir() else "file"}
            if child.is_file():
                with child.open('rb') as stream:
                    prefix = stream.read(512)
                hint = 'ELF' if prefix.startswith(b'\x7fELF') else 'PE/MZ' if prefix.startswith(b'MZ') else 'binary'
                if hint == 'binary':
                    try:
                        prefix.decode('utf-8')
                        if b'\x00' not in prefix: hint = 'UTF-8 text prefix'
                    except UnicodeError: pass
                entry.update(bytes=child.stat().st_size, format_hint=hint, format_hint_is_heuristic=True)
            entries.append(entry)
        content = json.dumps({"path": arguments["path"], "entries": entries}, ensure_ascii=False)
        return ToolOutcome(ToolReply(content), {"status": "ok", "entries": len(entries)})

    def _read_challenge_text(self, arguments: Mapping[str, Any]) -> ToolOutcome:
        target = self._checked_file(self.challenge_root, arguments["path"])
        if not target.is_file():
            raise ValueError("path is not a regular file")
        if target.stat().st_size > self.max_file_read_bytes:
            raise ValueError(f"file exceeds the {self.max_file_read_bytes}-byte text-read limit")
        data = self.reliability.input_bytes(arguments['path'], maximum=self.max_file_read_bytes)
        text = data.decode("utf-8")
        artifact = self.evidence.write_artifact(data, media_type="text/plain; charset=utf-8")
        content = json.dumps({"path": arguments["path"], "text": text, "evidence": artifact}, ensure_ascii=False)
        return ToolOutcome(ToolReply(content), {"status": "ok", "evidence": artifact,
            "coverage": {"source_kind": "file", "path": arguments["path"], "sha256": artifact["sha256"],
                         "offset": 0, "length": len(data), "filter": "none", "truncated": False}})

    def _run_command(self, arguments: Mapping[str, Any]) -> ToolOutcome:
        if self.runtime is None:
            return ToolOutcome(ToolReply("No approved isolated runtime is configured.", False), {"status": "runtime_unavailable"})
        argv = arguments["argv"]
        if not isinstance(argv, list) or not argv or len(argv) > 64 or any(not isinstance(item, str) or "\x00" in item for item in argv):
            raise ValueError("argv must be a non-empty list of at most 64 NUL-free strings")
        if sum(len(item.encode("utf-8")) for item in argv) > 16 * 1024:
            raise ValueError("argv exceeds the 16 KiB limit")
        result = self.runtime.execute(argv, timeout=self.command_timeout)
        stdout_artifact = self.evidence.write_artifact(result.stdout, media_type="text/plain; charset=utf-8")
        stderr_artifact = self.evidence.write_artifact(result.stderr, media_type="text/plain; charset=utf-8")
        command_artifact = self.evidence.write_artifact(json.dumps(argv, ensure_ascii=True).encode(), media_type="application/json")
        self.evidence.append("command_execution", argv=command_artifact, timeout_seconds=self.command_timeout,
                             exit_code=result.exit_code, timed_out=result.timed_out, truncated=result.truncated,
                             stdout_evidence=stdout_artifact, stderr_evidence=stderr_artifact)
        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")
        error_kind = ("tool_timeout" if result.timed_out else
                      "dependency_missing" if "ModuleNotFoundError: No module named" in stderr or result.exit_code in {126, 127} else
                      "api_usage_error" if "ImportError: cannot import name" in stderr else
                      "input_format_mismatch" if "JSONDecodeError" in stderr or "ctfbot input format mismatch:" in stderr else
                      "execution_failure" if result.exit_code else None)
        payload = {
            "exit_code": result.exit_code,
            "execution_state": "timed_out" if result.timed_out else "completed" if result.exit_code == 0 else "failed",
            "timed_out": result.timed_out,
            "truncated": result.truncated,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_evidence": stdout_artifact,
            "stderr_evidence": stderr_artifact,
        }
        content = json.dumps(payload, ensure_ascii=False)
        if len(content.encode("utf-8")) > self.max_model_output_bytes:
            payload = {
                "exit_code": result.exit_code,
                "execution_state": "timed_out" if result.timed_out else "completed" if result.exit_code == 0 else "failed",
                "timed_out": result.timed_out,
                "truncated": True,
                "output_preview": "",
                "stdout_evidence": stdout_artifact["artifact"],
                "stderr_evidence": stderr_artifact["artifact"],
            }
            preview = stdout + ("\n[stderr]\n" + stderr if stderr else "")
            rendered = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            low, high = 0, len(preview)
            while low < high:
                middle = (low + high + 1) // 2
                payload["output_preview"] = preview[:middle]
                candidate = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                if len(candidate.encode("utf-8")) <= self.max_model_output_bytes:
                    rendered = candidate
                    low = middle
                else:
                    high = middle - 1
            payload["output_preview"] = preview[:low]
            content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            if len(content.encode("utf-8")) > self.max_model_output_bytes:
                raise ValueError("configured tool output limit cannot hold the evidence summary")
        return ToolOutcome(ToolReply(content, result.exit_code == 0 and not result.timed_out), {
            "status": "timeout" if result.timed_out else "ok" if result.exit_code == 0 else "command_failed",
            "execution_state": payload['execution_state'],
            "exit_code": result.exit_code, "stdout_evidence": stdout_artifact, "stderr_evidence": stderr_artifact,
            "timed_out": result.timed_out, "truncated": result.truncated,
            "error_kind": error_kind,
            "coverage": {"source_kind": "command", "stdout_bytes": len(result.stdout), "stderr_bytes": len(result.stderr),
                         "filter": "none; runtime output caps apply", "truncated": result.truncated},
        })

    def _interactive_definitions(self) -> dict[str, tuple[ToolSpec, Handler]]:
        session_id = _schema_string("Session UUID returned by session_start; valid only in this run.")
        session_id["maxLength"] = 36
        return {
            "session_start": (
                ToolSpec("session_start", f"Start one bounded interactive process in this run's /work directory. {self.network_description}", {
                    "type": "object", "properties": {"argv": {
                        "type": "array", "items": {"type": "string", "maxLength": 4096},
                        "minItems": 1, "maxItems": 64,
                    }}, "required": ["argv"], "additionalProperties": False,
                }), self._session_start),
            "session_send": (
                ToolSpec("session_send", "Send UTF-8 text to an interactive process owned by this run.", {
                    "type": "object", "properties": {
                        "session_id": session_id,
                        "input": {"type": "string", "maxLength": 8192},
                    }, "required": ["session_id", "input"], "additionalProperties": False,
                }), self._session_send),
            "session_read": (
                ToolSpec("session_read", "Read bounded output from an interactive process. wait_seconds <=10 returns on new output; optional collect_seconds <=60 coalesces heartbeats until completion, error, output cap or deadline. Reading never resets output_idle_seconds.", {
                    "type": "object", "properties": {
                        "session_id": session_id,
                        "wait_seconds": {"type": "number", "minimum": 0, "maximum": 10},
                        "collect_seconds": {"type": "number", "minimum": 0, "maximum": 60},
                    }, "required": ["session_id"], "additionalProperties": False,
                }), self._session_read),
            "session_close": (
                ToolSpec("session_close", "Close an interactive process and persist its final transcript state.", {
                    "type": "object", "properties": {"session_id": session_id},
                    "required": ["session_id"], "additionalProperties": False,
                }), self._session_close),
        }

    def _session_start(self, arguments: Mapping[str, Any], *, background: bool = False) -> ToolOutcome:
        runtime = self.runtime
        if runtime is None:
            return ToolOutcome(ToolReply("No approved isolated runtime is configured.", False), {"status": "runtime_unavailable"})
        argv = arguments["argv"]
        if not isinstance(argv, list) or not argv or len(argv) > 64 or any(
            not isinstance(item, str) or "\x00" in item for item in argv
        ):
            raise ValueError("argv must be a non-empty list of at most 64 NUL-free strings")
        if sum(len(item.encode("utf-8")) for item in argv) > 16 * 1024:
            raise ValueError("argv exceeds the 16 KiB limit")
        session_id = str(uuid.uuid4())
        transcript = _SessionTranscript(self.evidence, session_id)
        with self._session_lock:
            self._session_transcripts[session_id] = transcript
        transcript.record_state("starting")
        try:
            start = runtime.start_script_session if background else runtime.start_interactive  # type: ignore[attr-defined]
            options = {}
            if background:
                deadline = getattr(self, "run_deadline", None)
                options["total_timeout"] = min(1800.0, max(0.1, deadline - time.monotonic())) if deadline else 1800.0
            start(
                session_id,
                argv,
                on_output=transcript.record_output,
                on_closed=transcript.record_closed,
                **options,
            )  # type: ignore[attr-defined]
        except Exception as exc:
            transcript.record_closed("start_failed", None)
            with self._session_lock:
                self._session_transcripts.pop(session_id, None)
            safe_message = f"Interactive process could not start ({type(exc).__name__})."
            return ToolOutcome(ToolReply(safe_message, False), {
                "status": "session_start_failed",
                "session_id": session_id,
                "transcript": transcript.relative_path,
            })
        transcript.record_state("started")
        payload = {"session_id": session_id, "status": "started", "transcript": transcript.relative_path}
        return ToolOutcome(ToolReply(json.dumps(payload)), {
            "status": "session_started", "session_id": session_id,
            "transcript": transcript.relative_path,
        })

    def _session_send(self, arguments: Mapping[str, Any]) -> ToolOutcome:
        runtime = self.runtime
        session_id = _checked_session_id(arguments["session_id"])
        transcript = self._session_transcript(session_id)
        value = arguments["input"]
        if not isinstance(value, str) or "\x00" in value:
            raise ValueError("interactive input must be NUL-free UTF-8 text")
        data = value.encode("utf-8")
        if not data or len(data) > 8 * 1024:
            raise ValueError("interactive input must contain 1 to 8192 UTF-8 bytes")
        transcript.record_input(data)
        if runtime is None:
            raise RuntimeError("interactive runtime is unavailable")
        runtime.send_interactive(session_id, data)  # type: ignore[attr-defined]
        payload = {"session_id": session_id, "status": "sent", "bytes": len(data)}
        return ToolOutcome(ToolReply(json.dumps(payload)), {
            "status": "session_input_sent", **payload,
            "transcript": transcript.relative_path,
        })

    def _session_read(self, arguments: Mapping[str, Any]) -> ToolOutcome:
        runtime = self.runtime
        session_id = _checked_session_id(arguments["session_id"])
        transcript = self._session_transcript(session_id)
        wait_seconds = arguments.get("wait_seconds", 0.25)
        if isinstance(wait_seconds, bool) or not isinstance(wait_seconds, (int, float)):
            raise ValueError("wait_seconds must be a number")
        if not 0 <= wait_seconds <= 10:
            raise ValueError("wait_seconds must be between 0 and 10")
        if runtime is None:
            raise RuntimeError("interactive runtime is unavailable")
        maximum_bytes = min(8 * 1024, max(128, self.max_model_output_bytes // 2))
        collect = float(arguments.get('collect_seconds', 0))
        deadline = min(time.monotonic()+collect, getattr(self, 'run_deadline', float('inf')))
        initial_wait = min(float(wait_seconds), max(0, (deadline if collect else getattr(self, 'run_deadline', float('inf')))-time.monotonic()))
        result = runtime.read_interactive(  # type: ignore[attr-defined]
            session_id,
            timeout=initial_wait,
            maximum_bytes=maximum_bytes,
        )
        chunks = [result.data]
        size = len(result.data)
        while collect and result.status == 'running' and not result.timed_out and not result.truncated and size < maximum_bytes:
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                break
            result = runtime.read_interactive(session_id, timeout=min(10,remaining), maximum_bytes=maximum_bytes-size)  # type: ignore[attr-defined]
            chunks.append(result.data)
            size += len(result.data)
        result = replace(result, data=b''.join(chunks))
        payload = {
            "session_id": session_id,
            "status": result.status,
            "output": result.data.decode("utf-8", errors="replace"),
            "output_evidence": transcript.latest_output,
            "transcript": transcript.relative_path,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "truncated": result.truncated,
            "elapsed_seconds": result.elapsed_seconds,
            "output_idle_seconds": result.output_idle_seconds,
            "execution_state": result.status,
        }
        status = "session_timeout" if result.timed_out else "session_output"
        return ToolOutcome(ToolReply(json.dumps(payload, ensure_ascii=False), not result.timed_out), {
            "status": status,
            "session_id": session_id,
            "bytes": len(result.data),
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "truncated": result.truncated,
            "elapsed_seconds": result.elapsed_seconds,
            "output_idle_seconds": result.output_idle_seconds,
            "output_evidence": transcript.latest_output,
            "transcript": transcript.relative_path,
        })

    def _session_close(self, arguments: Mapping[str, Any]) -> ToolOutcome:
        runtime = self.runtime
        session_id = _checked_session_id(arguments["session_id"])
        transcript = self._session_transcript(session_id)
        if runtime is None:
            raise RuntimeError("interactive runtime is unavailable")
        try:
            result = runtime.close_interactive(session_id)  # type: ignore[attr-defined]
        except Exception:
            transcript.record_closed("close_error", None)
            raise
        transcript.record_closed(result.status, result.exit_code)
        with self._session_lock:
            self._session_transcripts.pop(session_id, None)
        payload = {
            "session_id": session_id,
            "status": result.status,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "transcript": transcript.relative_path,
        }
        return ToolOutcome(ToolReply(json.dumps(payload)), {
            "status": "session_closed",
            **payload,
            "cleanup_status": "complete" if result.status in {"closed", "exited"} else result.status,
        })

    def _session_transcript(self, session_id: str) -> "_SessionTranscript":
        with self._session_lock:
            transcript = self._session_transcripts.get(session_id)
        if transcript is None:
            raise PermissionError("session ID is not owned by this run or has already been closed")
        return transcript

    def _submit_candidate(self, arguments: Mapping[str, Any]) -> ToolOutcome:
        candidate = arguments["candidate"]
        if not isinstance(candidate, str):
            raise ValueError("candidate must be a string")
        raw = candidate.encode("utf-8")
        if len(raw) > 4096:
            raise ValueError("candidate exceeds the 4096-byte limit")
        candidate_artifact = self.evidence.write_artifact(raw, media_type="text/plain; charset=utf-8")
        if self.oracle_path is None:
            return ToolOutcome(ToolReply(json.dumps({"status": "unverified", "verification_method": "none"})), {
                "status": "unverified",
                "verification_method": "none",
                "candidate_evidence": candidate_artifact,
            })
        accepted = verify_exact(self.oracle_path, candidate)
        status = "verified" if accepted else "rejected"
        result = {
            "status": status,
            "candidate_sha256": hashlib.sha256(raw).hexdigest(),
            "verification_method": "exact-string controller-only",
        }
        return ToolOutcome(ToolReply(json.dumps(result)), {**result, "candidate_evidence": candidate_artifact})


class _SessionTranscript:
    def __init__(self, evidence: EvidenceStore, session_id: str) -> None:
        self.evidence = evidence
        self.session_id = session_id
        session_root = evidence.run_dir / "sessions"
        if session_root.is_symlink():
            raise ValueError("session transcript directory must not be a symlink")
        session_root.mkdir(mode=0o700, exist_ok=True)
        os.chmod(session_root, 0o700)
        self.path = session_root / f"{session_id}.jsonl"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(self.path, flags, 0o600)
        os.close(fd)
        self.relative_path = self.path.relative_to(evidence.run_dir).as_posix()
        self._lock = threading.RLock()
        self._sequence = 0
        self._closed = False
        self._latest_output: dict[str, Any] | None = None

    @property
    def latest_output(self) -> dict[str, Any] | None:
        with self._lock:
            return dict(self._latest_output) if self._latest_output else None

    def record_input(self, data: bytes) -> None:
        self._record_io("input", data)

    def record_output(self, data: bytes) -> None:
        self._record_io("output", data)

    def record_state(self, state: str) -> None:
        with self._lock:
            if self._closed:
                return
            self._append_locked({
                "direction": "state",
                "state": state,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            })

    def record_closed(self, state: str, exit_code: int | None) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._append_locked({
                "direction": "state",
                "state": state,
                "exit_code": exit_code,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            })

    def _record_io(self, direction: str, data: bytes) -> None:
        with self._lock:
            artifact = self.evidence.write_artifact(data, media_type="application/octet-stream")
            if direction == "output":
                self._latest_output = artifact
            self._append_locked({
                "direction": direction,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "bytes": len(data),
                "artifact": artifact,
            })

    def _append_locked(self, record: dict[str, Any]) -> None:
        self._sequence += 1
        record = {"seq": self._sequence, **record}
        encoded = (json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        flags = os.O_WRONLY | os.O_APPEND
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(self.path, flags)
        with os.fdopen(fd, "ab") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        self.evidence.append(
            "session_transcript",
            session_id=self.session_id,
            transcript_path=self.relative_path,
            transcript_seq=self._sequence,
            direction=record["direction"],
            bytes=record.get("bytes", 0),
            artifact=record.get("artifact"),
            state=record.get("state"),
            exit_code=record.get("exit_code"),
        )


def _checked_session_id(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("session ID must be a UUID string")
    try:
        canonical = str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("session ID must be a UUID string") from exc
    if canonical != value:
        raise ValueError("session ID must be a canonical UUID string")
    return value
