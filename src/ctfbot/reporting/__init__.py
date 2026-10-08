'''Basic run reports derived only from persisted metadata and evidence.'''

from __future__ import annotations

import json
import os
import unicodedata
from pathlib import Path
from typing import Any


def generate_basic_report(run_dir: Path) -> Path:
    """Write a private Markdown summary without reading artifact contents."""
    if run_dir.is_symlink():
        raise ValueError("run directory must not be a symlink")
    resolved = run_dir.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("run directory must be a directory")
    metadata_path = resolved / "run.json"
    events_path = resolved / "events.jsonl"
    state_path = resolved / "run-state.json"
    for path in (metadata_path, events_path):
        if path.is_symlink() or not path.is_file():
            raise ValueError("run metadata and evidence must be regular files")
    run_state: dict[str, Any] | None = None
    if state_path.exists() or state_path.is_symlink():
        if state_path.is_symlink() or not state_path.is_file():
            raise ValueError("run lifecycle state must be a regular file")
        try:
            loaded_state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("run lifecycle state is invalid") from exc
        if not isinstance(loaded_state, dict) or loaded_state.get("schema_version") != 1:
            raise ValueError("run lifecycle state has an unsupported shape")
        run_state = loaded_state
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("run metadata is invalid") from exc
    events, incomplete_tail = _read_events(events_path)
    if not isinstance(metadata, dict) or any(not isinstance(event, dict) for event in events):
        raise ValueError("run metadata or events have an invalid shape")
    if run_state is not None and run_state.get("run_id") != metadata.get("run_id"):
        raise ValueError("run lifecycle state does not match run metadata")
    finished = [event for event in events if event.get("event_type") == "run_finished"]
    if not finished and run_state is None:
        raise ValueError("run has no finished event; report is available after the run completes")
    result = finished[-1] if finished else {
        "status": run_state.get("result_status") or f"incomplete ({run_state.get('state', 'unknown')})",
        "stop_reason": run_state.get("stop_reason") or "run did not record a terminal event",
    }
    lifecycle_state = run_state.get("state", "legacy") if run_state else "terminal event only"
    lifecycle_phase = run_state.get("phase", "unknown") if run_state else "unknown"
    recovery = run_state.get("recovery", {}) if run_state else metadata.get("recovery", {})

    lines = [
        "# ctfbot run report",
        "",
        f"- Run: `{_safe_inline(metadata.get('run_id', 'unknown'))}`",
        f"- Challenge: `{_safe_inline(metadata.get('challenge_id', 'unknown'))}`",
        f"- Status: `{_safe_inline(result.get('status', 'unknown'))}`",
        f"- Stop reason: `{_safe_inline(result.get('stop_reason', 'unknown'))}`",
        f"- Result contract: `{_safe_inline(metadata.get('result_contract_version', 1))}`",
        f"- Lifecycle: `{_safe_inline(lifecycle_state)}` / `{_safe_inline(lifecycle_phase)}`",
        f"- Runtime image: `{_safe_inline(metadata.get('runtime_image', 'unknown'))}`",
        f"- Import mode: `{_safe_inline(metadata.get('import_mode', 'unknown'))}`",
        "- Model data authorization: `recorded`" if metadata.get("model_data_authorized") is True
        else "- Model data authorization: `not recorded`",
        "- Automatic retry: `disabled`" if isinstance(recovery, dict) and recovery.get("automatic_retry") is False
        else "- Automatic retry: `unspecified`",
        "- Resume: `unsupported; review partial evidence and start a new run`"
        if isinstance(recovery, dict) and recovery.get("resume_supported") is False
        else "- Resume: `unspecified`",
        "",
    ]
    if incomplete_tail:
        lines.append("- Evidence log has an incomplete trailing event; that fragment was omitted.")
    verification = metadata.get("verification")
    if isinstance(verification, dict):
        lines.append(f"- Verification method: `{_safe_inline(verification.get('method', 'unknown'))}`")
        if verification.get("flag_format"):
            lines.extend([f"- Flag template: `{_safe_inline(verification['flag_format'])}`",
                          "- A format-matching candidate is not a confirmed correct flag."])
    service = metadata.get("local_service")
    if isinstance(service, dict):
        lines.extend([
            f"- Local service image: `{_safe_inline(service.get('image', 'unknown'))}`",
            f"- Local TCP endpoint: `challenge:{_safe_inline(service.get('port', 'unknown'))}`",
        ])
    packs = metadata.get("domain_packs", {})
    memory = metadata.get("memory", {})
    if isinstance(packs, dict) and packs.get("catalog_sha256"):
        lines.extend(["", "## Workflow and memory conditions", "",
                      f"- Pack catalog SHA-256: `{_safe_inline(packs['catalog_sha256'])}`"])
    if isinstance(memory, dict):
        lines.extend([f"- Memory mode: `{_safe_inline(memory.get('mode', 'unspecified'))}`",
                      f"- Memory namespaces: `{_safe_inline(', '.join(str(name) for name in memory.get('namespaces', [])) or 'none')}`",
                      f"- Reviewed memory versions: `{len(memory.get('versions', []))}`",
                      "- Automatic cross-run memory writes: `disabled`"])
    lines.extend(["", "## Inputs", ""])
    input_files = metadata.get("input_files", [])
    if isinstance(input_files, list) and input_files:
        for item in input_files:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"- `{_safe_inline(item.get('path', 'unknown'))}` — "
                f"{_safe_inline(item.get('bytes', 'unknown'))} bytes; "
                f"SHA-256 `{_safe_inline(item.get('sha256', 'unknown'))}`"
            )
    else:
        lines.append("- No input file records were present.")

    lines.extend(["", "## Run limits", ""])
    limits = metadata.get("limits", {})
    if isinstance(limits, dict) and limits:
        for key in sorted(limits):
            value = 'unlimited (count only)' if key == 'max_tool_calls' and limits[key] is None else limits[key]
            lines.append(f"- `{_safe_inline(key)}`: `{_safe_inline(value)}`")
    else:
        lines.append("- No run limits were present.")

    counts = result.get('call_counts')
    if isinstance(counts, dict):
        lines.extend(['', '## Tool request counts', '', '- Admitted means entered tool handling, not successful computation.'])
        for category in ('execution', 'observation', 'control'):
            row = counts.get(category, {})
            lines.append(f"- {category}: requested={_safe_inline(row.get('requested', 0))}, admitted={_safe_inline(row.get('admitted', 0))}, rejected={_safe_inline(row.get('rejected', 0))}")
    lines.extend(["", "## Recorded timeline", ""])
    lines.extend(["- `unsolved` means this attempt did not solve the challenge; it is not proof of impossibility.",
                  "- `candidate_unverified` records a completed attempt with a candidate, not confirmed correctness.",
                  "- Historical `format_only` terminal results retain their original meaning; contract v2 records candidates separately.",
                  "- Local checks, model-reported support and process exit codes never become trusted oracle verification.", ""])
    for event in events:
        event_type = event.get("event_type", "unknown")
        details = _timeline_details(event)
        suffix = f" — {details}" if details else ""
        lines.append(
            f"- #{_safe_inline(event.get('seq', '?'))} "
            f"`{_safe_inline(event.get('timestamp_utc', ''))}` "
            f"`{_safe_inline(event_type)}`{suffix}"
        )

    evidence_refs = sorted(_collect_artifact_refs(events))
    lines.extend(["", "## Evidence references", ""])
    if evidence_refs:
        lines.extend(f"- `{_safe_inline(ref)}`" for ref in evidence_refs)
    else:
        lines.append("- No artifact references were recorded.")

    report_path = resolved / "report.md"
    if report_path.is_symlink():
        raise ValueError("report destination must not be a symlink")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(report_path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(report_path, 0o600)
    return report_path


def _timeline_details(event: dict[str, Any]) -> str:
    event_type = event.get("event_type")
    if event_type == 'completion_recorded':
        return f"explicit completion `{_safe_inline(event.get('outcome'))}`; unresolved items `{event.get('unresolved_count', 0)}`"
    if event_type == 'controller_decision':
        return f"controller `{_safe_inline(event.get('action'))}`: `{_safe_inline(event.get('reason'))}`"
    if event_type == 'candidate_recorded':
        return f"candidate `{_safe_inline(event.get('candidate_id'))}`; `{_safe_inline(event.get('status'))}`; correctness unverified unless controller exact verification passed"
    if event_type == 'candidate_check_recorded':
        return f"local check `{_safe_inline(event.get('check_id'))}` for `{_safe_inline(event.get('candidate_id'))}`; passed `{event.get('passed')}`; not trusted verification"
    if event_type == "tool_call":
        return f"tool `{_safe_inline(event.get('name', 'unknown'))}`"
    if event_type == "tool_result":
        result = event.get("result")
        if isinstance(result, dict):
            parts = [f"tool `{_safe_inline(event.get('name', 'unknown'))}`"]
            if "status" in result:
                parts.append(f"status `{_safe_inline(result['status'])}`")
            if "verification_method" in result:
                parts.append(f"verifier `{_safe_inline(result['verification_method'])}`")
            return "; ".join(parts)
    if event_type == "run_finished":
        return (
            f"turns `{_safe_inline(event.get('turns', '?'))}`; "
            f"tool calls `{_safe_inline(event.get('tool_calls', '?'))}`"
        )
    if event_type == "run_state_changed":
        parts = [
            f"state `{_safe_inline(event.get('state', 'unknown'))}`",
            f"phase `{_safe_inline(event.get('phase', 'unknown'))}`",
        ]
        if event.get("stop_reason"):
            parts.append(f"reason `{_safe_inline(event['stop_reason'])}`")
        return "; ".join(parts)
    if event_type == "remote_lifecycle":
        details = (f"remote state `{_safe_inline(event.get('state', 'unknown'))}`; "
                f"endpoint `{_safe_inline(event.get('endpoint', 'unknown'))}`; "
                f"grant `{_safe_inline(event.get('grant_sha256', 'unknown'))}`; "
                f"status `{_safe_inline(event.get('status', 'unknown'))}`")
        if event.get("recovery_journal"):
            details += f"; recovery journal `{_safe_inline(event['recovery_journal'])}`"
        return details
    if event_type == "service_lifecycle":
        details = (f"service state `{_safe_inline(event.get('state', 'unknown'))}`; "
                   f"endpoint `{_safe_inline(event.get('endpoint', 'unknown'))}`")
        if event.get("execution_profile_sha256"):
            details += f"; execution profile `{_safe_inline(event['execution_profile_sha256'])}`"
        if event.get("recovery_journal"):
            details += f"; recovery journal `{_safe_inline(event['recovery_journal'])}`"
        return details
    if event_type in {"provider_error", "run_failed"}:
        error_type = event.get("kind", event.get("error_type", "unknown"))
        return f"error type `{_safe_inline(error_type)}`"
    if event_type in {"workflow_read", "workflow_execution"}:
        return f"pack `{_safe_inline(event.get('pack', 'unknown'))}`; operation `{_safe_inline(event.get('operation', 'read'))}`"
    if event_type == "domain_routing":
        return f"labels `{_safe_inline(', '.join(str(label) for label in event.get('labels', [])) or 'unknown')}`; confidence `{_safe_inline(event.get('confidence', 'unknown'))}`; network authority unchanged"
    if event_type in {"script_saved", "script_execution"}:
        return f"script `{_safe_inline(event.get('path', 'unknown'))}`; source `{_safe_inline(event.get('source', {}).get('sha256', 'unknown'))}`"
    if event_type == "artifact_lineage":
        return f"artifact `{_safe_inline(event.get('artifact', {}).get('sha256', 'unknown'))}`; declared input parents `{len(event.get('parents', []))}`"
    if event_type == "memory_used":
        return f"reviewed memory `{_safe_inline(event.get('memory_id', 'unknown'))}`; namespace `{_safe_inline(event.get('namespace', 'unknown'))}`"
    return ""


def _collect_artifact_refs(value: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, dict):
        artifact = value.get("artifact")
        if isinstance(artifact, str) and artifact.startswith("artifacts/") and ".." not in Path(artifact).parts:
            refs.add(artifact)
        transcript = value.get("transcript_path")
        if (
            isinstance(transcript, str)
            and transcript.startswith("sessions/")
            and not Path(transcript).is_absolute()
            and ".." not in Path(transcript).parts
        ):
            refs.add(transcript)
        for item in value.values():
            refs.update(_collect_artifact_refs(item))
    elif isinstance(value, list):
        for item in value:
            refs.update(_collect_artifact_refs(item))
    return refs


def _read_events(path: Path) -> tuple[list[dict[str, Any]], bool]:
    try:
        raw_lines = path.read_bytes().splitlines(keepends=True)
    except OSError as exc:
        raise ValueError("run event evidence is unavailable") from exc
    events: list[dict[str, Any]] = []
    incomplete_tail = False
    for index, raw_line in enumerate(raw_lines):
        complete = raw_line.endswith(b"\n")
        line = raw_line[:-1] if complete else raw_line
        if not line.strip():
            continue
        try:
            event = json.loads(line.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            if index == len(raw_lines) - 1 and not complete:
                incomplete_tail = True
                break
            raise ValueError("run event evidence is invalid") from exc
        if not isinstance(event, dict):
            raise ValueError("run event evidence has an invalid shape")
        events.append(event)
    return events, incomplete_tail


def _safe_inline(value: Any) -> str:
    text = str(value)
    visible: list[str] = []
    for char in text:
        category = unicodedata.category(char)
        if category in {"Cc", "Cf", "Cs", "Zl", "Zp"}:
            codepoint = ord(char)
            visible.append(f"\\u{codepoint:04x}" if codepoint <= 0xFFFF else f"\\U{codepoint:08x}")
        elif char == "`":
            visible.append("\\`")
        elif char == "\\":
            visible.append("\\\\")
        else:
            visible.append(char)
    return "".join(visible)
