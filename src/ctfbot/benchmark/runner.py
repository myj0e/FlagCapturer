"""Repeatable Stage A batch runner and redacted aggregate summary writer."""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from ctfbot.agent.loop import RunLimits, RunResult
from ctfbot.application.baseline import run_baseline, validate_baseline_snapshot
from ctfbot.evidence.redaction import redact_sensitive_text
from ctfbot.model_adapters.protocol import ModelSession
from ctfbot.runtime.docker import validate_image_reference


@dataclass(frozen=True, slots=True)
class BaselineCase:
    challenge_id: str
    workspace: Path
    oracle: Path


@dataclass(frozen=True, slots=True)
class BatchResult:
    batch_id: str
    status: str
    summary_path: str
    completed_runs: int
    verified_runs: int
    remaining_model_turns: int


def load_case_set(path: Path) -> tuple[tuple[BaselineCase, ...], tuple[str, ...]]:
    path = path.resolve(strict=True)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("case-set must be a schema_version 1 JSON object")
    raw_cases = value.get("cases")
    raw_repeat = value.get("repeat_challenges", [])
    if not isinstance(raw_cases, list) or not isinstance(raw_repeat, list):
        raise ValueError("case-set requires cases and repeat_challenges arrays")
    cases: list[BaselineCase] = []
    seen: set[str] = set()
    for record in raw_cases:
        if not isinstance(record, dict) or set(record) != {"challenge_id", "workspace", "oracle"}:
            raise ValueError("each case must contain only challenge_id, workspace, and oracle paths")
        challenge_id = record["challenge_id"]
        if not isinstance(challenge_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,79}", challenge_id):
            raise ValueError("case has an invalid challenge_id")
        if challenge_id in seen:
            raise ValueError(f"duplicate case ID: {challenge_id}")
        seen.add(challenge_id)
        if not isinstance(record["workspace"], str) or not isinstance(record["oracle"], str):
            raise ValueError("workspace and oracle paths must be strings")
        workspace = (path.parent / record["workspace"]).resolve(strict=True)
        oracle = (path.parent / record["oracle"]).resolve(strict=True)
        cases.append(BaselineCase(challenge_id, workspace, oracle))
    if not cases or len(cases) > 20:
        raise ValueError("Stage A case-set must contain 1 to 20 cases")
    if any(not isinstance(item, str) for item in raw_repeat):
        raise ValueError("repeat_challenges must contain challenge ID strings")
    repeat_ids = tuple(raw_repeat)
    if len(set(repeat_ids)) != len(repeat_ids) or any(item not in seen for item in repeat_ids):
        raise ValueError("repeat_challenges must contain unique IDs that exist in cases")
    if len(cases) < 10:
        raise ValueError("Stage A baseline requires at least 10 formally admitted cases")
    if len(repeat_ids) != 3:
        raise ValueError("Stage A baseline requires exactly three selected repeat challenges")
    return tuple(cases), repeat_ids


def run_batch(
    *,
    cases: tuple[BaselineCase, ...],
    repeat_challenges: tuple[str, ...],
    model_factory: Callable[[], ModelSession],
    model_metadata: dict[str, Any],
    runtime_image: str,
    runs_root: Path,
    limits: RunLimits = RunLimits(),
    max_total_model_turns: int,
) -> BatchResult:
    if not 0 < max_total_model_turns <= 690:
        raise ValueError("Stage A batch cap must be between 1 and 690 model turns")
    if not 10 <= len(cases) <= 20:
        raise ValueError("Stage A baseline requires 10 to 20 formally admitted cases")
    if len(repeat_challenges) != 3 or len(set(repeat_challenges)) != 3:
        raise ValueError("Stage A baseline requires exactly three unique repeat challenges")
    by_id = {case.challenge_id: case for case in cases}
    if len(by_id) != len(cases) or any(challenge_id not in by_id for challenge_id in repeat_challenges):
        raise ValueError("batch case IDs or repeat selection are invalid")
    # Preflight the full set before opening a provider or launching Docker.
    snapshots: dict[str, dict[str, Any]] = {}
    for case in cases:
        _workspace, _oracle, provenance, _task = validate_baseline_snapshot(
            case.workspace, case.oracle, runs_root / "preflight"
        )
        if provenance.get("challenge_id") != case.challenge_id:
            raise ValueError(f"case ID does not match workspace provenance: {case.challenge_id}")
        if provenance.get("model_data_authorized") is not True:
            raise ValueError(f"model data authorization is missing for case: {case.challenge_id}")
        authorization_basis = provenance.get("model_data_authorization_basis")
        if not isinstance(authorization_basis, str) or not authorization_basis.strip():
            raise ValueError(f"model data authorization basis is missing for case: {case.challenge_id}")
        snapshots[case.challenge_id] = provenance
    validate_image_reference(runtime_image)
    jobs = [(case.challenge_id, 1) for case in cases]
    jobs.extend((challenge_id, 2) for challenge_id in repeat_challenges)
    raw_categories = [snapshots[case.challenge_id].get("category") for case in cases]
    if any(not isinstance(category, str) or not category.strip() for category in raw_categories):
        raise ValueError("every Stage A case must record a non-empty category")
    categories = set(raw_categories)
    repeated_categories = {snapshots[challenge_id].get("category") for challenge_id in repeat_challenges}
    if len(categories) < 4:
        raise ValueError("Stage A baseline requires at least four challenge categories")
    if len(repeated_categories) < 3 or None in repeated_categories:
        raise ValueError("the three repeat challenges must cover three distinct recorded categories")

    runs_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(runs_root, 0o700)
    batch_id = str(uuid.uuid4())
    batch_dir = runs_root / f"batch-{batch_id}"
    batch_dir.mkdir(mode=0o700)
    os.chmod(batch_dir, 0o700)
    summary_path = batch_dir / "summary.json"
    remaining = max_total_model_turns
    records: list[dict[str, Any]] = []
    total_runs = len(jobs)

    def write_summary(status: str) -> None:
        payload = {
            "schema_version": 1,
            "batch_id": batch_id,
            "status": status,
            "model": model_metadata,
            "runtime_image": runtime_image,
            "limits": {
                "per_run_max_turns": limits.max_turns,
                "per_run_max_tool_calls": limits.max_tool_calls,
                "wall_time_seconds": limits.wall_time_seconds,
                "max_total_model_turns": max_total_model_turns,
            },
            "case_ids": [case.challenge_id for case in cases],
            "repeat_challenges": list(repeat_challenges),
            "categories": sorted(str(item) for item in categories if item),
            "runs": records,
            "usage_totals": _sum_usage(records),
            "remaining_model_turns": remaining,
        }
        fd, name = tempfile.mkstemp(prefix=".summary-", suffix=".tmp", dir=batch_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(name, 0o600)
            os.replace(name, summary_path)
        finally:
            try:
                os.unlink(name)
            except FileNotFoundError:
                pass

    write_summary("running")
    for index, (challenge_id, replicate) in enumerate(jobs, start=1):
        if remaining <= 0:
            records.append({"challenge_id": challenge_id, "replicate": replicate, "status": "not_run_budget_exhausted"})
            continue
        case = by_id[challenge_id]
        run_limits = replace(limits, max_turns=min(limits.max_turns, remaining))
        allocation = run_limits.max_turns
        remaining -= allocation
        try:
            result: RunResult = run_baseline(
                workspace=case.workspace,
                oracle_path=case.oracle,
                runtime_image=runtime_image,
                runs_root=batch_dir / f"case-{challenge_id}-replicate-{replicate}",
                model_factory=model_factory,
                model_metadata={**model_metadata, "replicate": replicate, "batch_id": batch_id},
                limits=run_limits,
            )
            used_turns = min(allocation, result.turns)
            remaining += allocation - used_turns
            records.append({
                "challenge_id": challenge_id,
                "replicate": replicate,
                "status": result.status,
                "stop_reason": result.stop_reason,
                "turns": result.turns,
                "tool_calls": result.tool_calls,
                "verified": result.verified,
                "usage_totals": result.usage_totals,
                "run_dir": result.run_dir,
            })
        except Exception as exc:
            # run_baseline owns lazy model construction and writes its lifecycle
            # record after preflight; exceptions here happened before a run began.
            remaining += allocation
            records.append({
                "challenge_id": challenge_id,
                "replicate": replicate,
                "status": "environment_or_controller_error",
                "error_type": type(exc).__name__,
                "message": redact_sensitive_text(str(exc), limit=400),
            })
        write_summary("running" if index < total_runs else "complete")

    verified = sum(1 for row in records if row.get("verified") is True)
    status = "complete" if all(row.get("status") != "not_run_budget_exhausted" for row in records) else "budget_exhausted"
    write_summary(status)
    completed_runs = sum(1 for row in records if row.get("status") != "not_run_budget_exhausted")
    return BatchResult(batch_id, status, str(summary_path), completed_runs, verified, remaining)


def _sum_usage(records: list[dict[str, Any]]) -> dict[str, int | float]:
    totals: dict[str, int | float] = {}
    for record in records:
        usage = record.get("usage_totals")
        if not isinstance(usage, dict):
            continue
        for key, value in usage.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                totals[str(key)] = totals.get(str(key), 0) + value
    return totals
