"""Versioned D-stage datasets and bounded evaluation through the shared service."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
import uuid
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from ctfbot.agent.loop import RunLimits
from ctfbot.application.control import RunControl
from ctfbot.application.service import LocalChallengeService
from ctfbot.application.baseline import validate_baseline_snapshot
from ctfbot.packs.catalog import CATEGORIES, category
from ctfbot.runtime.service_recovery import read_private, private_directory, write_private
from ctfbot.reporting import _safe_inline


def load_dataset(path: Path) -> dict[str, Any]:
    if path.stat().st_size > 262144:
        raise ValueError("dataset manifest exceeds 256 KiB")
    value = read_private(path)
    fields = {"schema_version", "dataset_id", "version", "split", "license", "cases"}
    if set(value) != fields or value["schema_version"] != 2 or value["split"] not in {"development", "holdout"}:
        raise ValueError("expected a version-2 development/holdout dataset")
    if any(not isinstance(value[key], str) or not value[key].strip() for key in ("dataset_id", "version", "license")):
        raise ValueError("dataset must record its identity, version and license")
    if not isinstance(value["cases"], list) or not 1 <= len(value["cases"]) <= 100:
        raise ValueError("dataset requires 1 to 100 cases")
    seen = set()
    for item in value["cases"]:
        required = {"challenge_id", "workspace", "oracle", "category", "labels", "provenance_sha256", "exposure", "contamination", "mechanism"}
        if not isinstance(item, dict) or set(item) != required:
            raise ValueError("dataset case fields do not match schema")
        identity = item["challenge_id"]
        if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,79}", identity) or identity in seen:
            raise ValueError("dataset case IDs must be unique and canonical")
        seen.add(identity)
        if (item["category"] not in CATEGORIES or not isinstance(item["labels"], list)
                or not item["labels"] or any(label not in CATEGORIES for label in item["labels"])
                or item["category"] not in item["labels"]):
            raise ValueError("case requires a primary category and explicit supported labels")
        if item["exposure"] not in {"public", "adapted", "private", "authored"}:
            raise ValueError("case exposure must distinguish public/adapted/private/authored")
        for key in ("workspace", "oracle", "contamination", "mechanism"):
            if not isinstance(item[key], str) or not item[key].strip():
                raise ValueError(f"case requires {key}")
        if not isinstance(item["provenance_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", item["provenance_sha256"]):
            raise ValueError("case provenance hash must be pinned")
        for key in ("workspace", "oracle"):
            source = path.parent / item[key]
            if source.is_symlink():
                raise ValueError("dataset paths must not be symlinks")
            item[key] = source.resolve(strict=True)
    value["manifest_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return value


def audit_separation(development: Path, holdout: Path) -> dict[str, Any]:
    left, right = load_dataset(development), load_dataset(holdout)
    if left["split"] != "development" or right["split"] != "holdout" or left["dataset_id"] == right["dataset_id"]:
        raise ValueError("separation audit requires distinct development and holdout datasets")
    def fingerprints(dataset):
        ids, inputs = set(), set()
        for case in dataset["cases"]:
            ids.add(case["challenge_id"])
            provenance = json.loads((case["workspace"] / "provenance.json").read_text())
            if hashlib.sha256((case["workspace"] / "provenance.json").read_bytes()).hexdigest() != case["provenance_sha256"]:
                raise ValueError("case provenance changed during split audit")
            inputs.update(item["sha256"] for item in provenance["input_files"])
        return ids, inputs
    left_ids, left_hashes = fingerprints(left)
    right_ids, right_hashes = fingerprints(right)
    return {"schema_version": 1, "development": left["manifest_sha256"], "holdout": right["manifest_sha256"],
            "duplicate_case_ids": sorted(left_ids & right_ids), "duplicate_input_hashes": sorted(left_hashes & right_hashes),
            "disjoint": not (left_ids & right_ids or left_hashes & right_hashes),
            "claim": "byte/identity separation only; not proof of semantic independence or training exclusion"}


def _run_metrics(run_dir: Path) -> dict[str, Any]:
    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines()]
    candidates = sum(event.get("name") == "candidate_submit" and event.get("event_type") == "tool_result" for event in events)
    actions = [(event.get('name'), event.get("arguments_sha256")) for event in events if event.get("event_type") == "tool_call"]
    finished = [event for event in events if event.get("event_type") == "run_finished"]
    failures = [event for event in events if event.get("event_type") == "run_failed"]
    lifecycle = read_private(run_dir / "run-state.json")
    candidate_records = [e for e in events if e.get('event_type') == 'candidate_recorded']
    checks = [e for e in events if e.get('event_type') == 'candidate_check_recorded']
    from ctfbot.reporting.replay import _artifact
    provenance_count = 0
    for e in candidate_records:
        record = json.loads(_artifact(run_dir, e['details']))
        provenance_count += bool(record.get('observation_ids')) and bool(record.get('derivation'))
    return {"candidate_submissions": candidates, "repeated_actions": len(actions) - len(set(actions)),
            "call_counts": finished[-1].get('call_counts') if finished else None,
            "actual_command_executions": sum(e.get('event_type') == 'command_execution' for e in events),
            "reliability_contract_version": 2 if any(e.get('result_contract_version') == 2 for e in finished) else 1,
            "candidate_records": len(candidate_records), "candidate_with_provenance": provenance_count,
            "candidate_with_local_check": len({e.get('candidate_id') for e in checks}),
            "local_checks": len(checks), "local_checks_passed": sum(e.get('passed') is True for e in checks),
            "source_inconsistent_experiments": sum(e.get('event_type') == 'experiment_recorded' and e.get('source_consistent') is False for e in events),
            "model_reported_supported_claims": sum(e.get('event_type') == 'claim_recorded' and e.get('state') == 'supported' for e in events),
            "explicit_completions": sum(e.get('event_type') == 'completion_recorded' for e in events),
            "elapsed_seconds": finished[-1].get("elapsed_seconds") if finished else None,
            "cleanup_status": lifecycle.get("cleanup_status", "unknown"),
            "policy_denials": sum(event.get("event_type") == "tool_result" and event.get("result", {}).get("status") == "policy_denied" for event in events),
            "environment_error": any(event.get("failure_class") == "environment_error" for event in failures)}


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    started = [row for row in rows if row.get("run_id")]
    verified = sum(row.get("verified") is True for row in rows)
    environment = sum(row.get("environment_error") is True for row in rows)
    provider = sum(row.get("status") == "provider_error" for row in rows)
    # A verified candidate can be followed by a cleanup/provider failure. Keep
    # that run in this denominator while still reporting the failure separately.
    excluded_started = sum(not row.get("verified") and
                           (row.get("environment_error") is True or row.get("status") == "provider_error")
                           for row in started)
    elapsed = sorted(row["elapsed_seconds"] for row in rows if isinstance(row.get("elapsed_seconds"), (int, float)))
    usage: dict[str, float] = {}
    for row in rows:
        for key, value in row.get("usage_totals", {}).items():
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                usage[key] = usage.get(key, 0) + value
    return {"scheduled": len(rows), "started": len(started), "verified": verified,
            "candidate_only": sum(bool(row.get("candidate_submissions")) and not row.get("verified") for row in rows),
            "environment_errors": environment, "provider_errors": provider,
            "unsolved_started": sum(not row.get("verified") and not row.get("environment_error") and row.get("status") != "provider_error" for row in started),
            "cleanup_failures": sum(bool(row.get("cleanup_errors")) for row in rows),
            "not_run": sum(str(row.get("status", "")).startswith("not_run") for row in rows),
            "failed_before_start": sum(not row.get("run_id") and row.get("environment_error") is True for row in rows),
            "verified_over_scheduled": [verified, len(rows)],
            "verified_over_eligible_started": [verified, max(0, len(started) - excluded_started)],
            "latency_seconds": {"mean": sum(elapsed)/len(elapsed), "min": elapsed[0], "max": elapsed[-1],
                                "p50": elapsed[(len(elapsed)-1)//2], "p95": elapsed[min(len(elapsed)-1, math.ceil(len(elapsed)*.95)-1)]} if elapsed else None,
            "usage_totals": usage, "cost_per_verified": None,
            "cost_status": "unknown; provider billing semantics/rates have not been accepted",
            "policy_denials": sum(row.get("policy_denials", 0) for row in rows),
            "repeated_actions": sum(row.get("repeated_actions", 0) for row in rows),
            "reliability": {"v2_runs": sum(row.get('reliability_contract_version') == 2 for row in rows),
                **{key: sum(row.get(key, 0) for row in rows) for key in (
                    'candidate_records','candidate_with_provenance','candidate_with_local_check',
                    'local_checks','local_checks_passed','source_inconsistent_experiments',
                    'model_reported_supported_claims','explicit_completions')},
                "interpretation": "Structured provenance/check/stop counts only; not a reasoning-quality or solve-rate guarantee."}}


def run_evaluation(*, dataset_path: Path, service: LocalChallengeService, runtime_image: str,
                   output: Path, limits: RunLimits, max_total_turns: int, max_total_tool_calls: int,
                   max_total_wall_seconds: int = 3600, repetitions: int = 1,
                   development_reference: Path | None = None, control: RunControl | None = None) -> Path:
    if (type(max_total_turns) is not int or not 1 <= max_total_turns <= 690
            or type(max_total_tool_calls) is not int or not 1 <= max_total_tool_calls <= 5000
            or type(max_total_wall_seconds) is not int or not 1 <= max_total_wall_seconds <= 86400
            or type(repetitions) is not int or not 1 <= repetitions <= 3):
        raise ValueError("evaluation requires bounded turn/tool/wall budgets and 1 to 3 repetitions")
    dataset = load_dataset(dataset_path)
    control = control or RunControl()
    split_audit = None
    if dataset["split"] == "holdout":
        if service.evaluation_mode != "blind":
            raise PermissionError("holdout evaluation must use blind memory policy")
        if development_reference is None:
            raise ValueError("holdout evaluation requires an explicit development dataset for split audit")
        split_audit = audit_separation(development_reference, dataset_path)
        if not split_audit["disjoint"]:
            raise ValueError("development and holdout share case IDs or input bytes")
    snapshots = {}
    for case in dataset["cases"]:
        preview = service.preview(case["workspace"], case["oracle"], runtime_image, output)
        if not preview.start_allowed:
            raise PermissionError(f"case cannot start: {case['challenge_id']}: {preview.start_block_reason or 'model transmission unauthorized'}")
        _, _, provenance, _ = validate_baseline_snapshot(case["workspace"], case["oracle"], output)
        actual_hash = hashlib.sha256((case["workspace"] / "provenance.json").read_bytes()).hexdigest()
        if (case["challenge_id"] != provenance["challenge_id"] or actual_hash != case["provenance_sha256"]
                or category(str(provenance.get("category", ""))) != case["category"]):
            raise ValueError("dataset case identity/category/provenance differs from admitted snapshot")
        snapshots[case["challenge_id"]] = {"provenance_sha256": actual_hash,
                                          "oracle_sha256": hashlib.sha256(case["oracle"].read_bytes()).hexdigest(),
                                          "memory_snapshot_sha256": preview.memory_policy["snapshot_sha256"] if preview.memory_policy else None}
    private_directory(output)
    batch_dir = private_directory(output / f"evaluation-{uuid.uuid4()}")
    summary_path = batch_dir / "summary.json"
    rows: list[dict[str, Any]] = []
    jobs = [(case, repeat) for repeat in range(1, repetitions+1) for case in dataset["cases"]]
    remaining_turns, remaining_tools = max_total_turns, max_total_tool_calls
    halt_reason = None
    deadline = time.monotonic() + max_total_wall_seconds
    conditions = {"runtime_image": runtime_image, "per_run_limits": asdict(limits),
                  "max_total_turns": max_total_turns, "max_total_tool_calls": max_total_tool_calls,
                  "max_total_wall_seconds": max_total_wall_seconds, "repetitions": repetitions,
                  "memory_mode": service.evaluation_mode, "memory_namespaces": list(service.memory_namespaces),
                  "automatic_retry": False}
    def persist(status):
        write_private(summary_path, {"schema_version": 2, "status": status,
                      "dataset": {key: dataset[key] for key in ("dataset_id", "version", "split", "license", "manifest_sha256")},
                      "conditions": conditions, "split_audit": split_audit, "runs": rows,
                      "aggregate": _aggregate(rows), "categories": {name: _aggregate([row for row in rows if row["category"] == name]) for name in CATEGORIES},
                      "per_challenge": {case["challenge_id"]: _aggregate([row for row in rows if row["challenge_id"] == case["challenge_id"]]) for case in dataset["cases"]},
                      "remaining_turns": remaining_turns, "remaining_tool_calls": remaining_tools,
                      "generalization_claim": "none; public mechanisms/authored fixtures are engineering evidence only"})
        lines = ["# Controlled evaluation", "", f"Status: `{_safe_inline(status)}`", "",
                 f"Dataset: `{_safe_inline(dataset['dataset_id'])}` / `{_safe_inline(dataset['version'])}` / `{dataset['split']}`", "",
                 "| Category | Scheduled | Verified | Candidate only | Unsolved | Environment errors | Provider errors | Not run |",
                 "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for name in CATEGORIES:
            metrics = _aggregate([row for row in rows if row["category"] == name])
            lines.append(f"| {name} | " + " | ".join(str(metrics[key]) for key in
                         ("scheduled", "verified", "candidate_only", "unsolved_started", "environment_errors", "provider_errors", "not_run")) + " |")
        lines.extend(["", "Counts refer to runs; repetitions are reported separately in summary.json.", "",
                      "Cost: unknown; no accepted provider billing conversion. No generalization or training-exclusion claim.",
                      "Command replay and live-model category acceptance are separate from evaluation execution.", ""])
        fd = os.open(batch_dir / "summary.md", os.O_CREAT | os.O_NOFOLLOW | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write("\n".join(lines))
    persist("running")
    for case, repeat in jobs:
        row = {key: case[key] for key in ("challenge_id", "category", "labels", "exposure", "contamination", "mechanism")}
        row["replicate"] = repeat
        remaining_wall = deadline - time.monotonic()
        if halt_reason or control.cancelled or remaining_turns <= 0 or remaining_tools <= 0 or remaining_wall <= 0:
            row["status"] = f"not_run_{halt_reason}" if halt_reason else "not_run_cancelled" if control.cancelled else "not_run_budget_exhausted"
            rows.append(row); persist("running"); continue
        allocation = replace(limits, max_turns=min(limits.max_turns, remaining_turns),
                             max_tool_calls=min(limits.max_tool_calls, remaining_tools) if limits.max_tool_calls is not None else remaining_tools,
                             wall_time_seconds=min(limits.wall_time_seconds, remaining_wall))
        remaining_turns -= allocation.max_turns
        remaining_tools -= allocation.max_tool_calls
        try:
            frozen = snapshots[case["challenge_id"]]
            if (hashlib.sha256((case["workspace"] / "provenance.json").read_bytes()).hexdigest() != frozen["provenance_sha256"]
                    or hashlib.sha256(case["oracle"].read_bytes()).hexdigest() != frozen["oracle_sha256"]):
                raise ValueError("case/oracle changed after evaluation preflight")
            current = service.preview(case["workspace"], case["oracle"], runtime_image, batch_dir / "runs", allocation)
            if not current.start_allowed or not current.memory_policy or current.memory_policy["snapshot_sha256"] != frozen["memory_snapshot_sha256"]:
                raise ValueError("execution policy or approved memory changed after evaluation preflight")
            result = service.run(case["workspace"], case["oracle"], runtime_image, batch_dir / "runs", allocation, control=control)
            if result.status == "user_cancelled":
                control.cancel()
            remaining_turns += max(0, allocation.max_turns - result.turns)
            remaining_tools += max(0, allocation.max_tool_calls - result.tool_calls)
            row.update(run_id=result.run_id, run_dir=result.run_dir, status=result.status, stop_reason=result.stop_reason,
                       turns=result.turns, tool_calls=result.tool_calls, verified=result.verified,
                       usage_totals=result.usage_totals, cleanup_errors=list(result.cleanup_errors))
            row.update(_run_metrics(Path(result.run_dir)))
            run_metadata = read_private(Path(result.run_dir) / "run.json")
            row["conditions"] = {key: run_metadata.get(key) for key in ("provider", "domain_packs", "memory", "source_tree_sha256", "provenance_sha256")}
            if run_metadata.get("memory", {}).get("snapshot_sha256") != frozen["memory_snapshot_sha256"]:
                row.update(status="conditions_changed", verified=False, environment_error=True)
                halt_reason = "conditions_changed"
            if row.get("cleanup_status") not in {"complete", "recovered"} or row.get("cleanup_errors"):
                row["cleanup_errors"] = [*row.get("cleanup_errors", []), f"cleanup_status:{row.get('cleanup_status', 'unknown')}"]
                halt_reason = "cleanup_required"
        except KeyboardInterrupt:
            control.cancel()
            row.update(status="not_run_cancelled", error_type="KeyboardInterrupt")
        except Exception as exc:
            # Reserve the full allocation if the controller cannot determine usage.
            row.update(status="environment_or_controller_error", environment_error=True, verified=False, error_type=type(exc).__name__)
        rows.append(row)
        persist("running")
    persist("complete" if all(not row["status"].startswith("not_run") for row in rows) else "incomplete")
    return summary_path
