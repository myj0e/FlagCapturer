"""Provider-neutral, budgeted single-agent loop for Stage A baselines."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ctfbot.evidence.store import EvidenceStore
from ctfbot.evidence.redaction import redact_sensitive_text
from ctfbot.application.control import RunControl
from ctfbot.model_adapters.protocol import ModelSession, ToolCall, ToolReply, normalize_usage
from ctfbot.tools.registry import ToolRegistry


class _SandboxUnavailable(RuntimeError):
    """Abort a model turn after its persistent runtime has been lost."""


@dataclass(frozen=True, slots=True)
class RunLimits:
    max_turns: int = 30
    max_tool_calls: int | None = None
    wall_time_seconds: int = 1800
    tool_timeout_seconds: int = 120
    per_tool_output_bytes: int = 16 * 1024
    total_model_output_bytes: int = 256 * 1024

    def __post_init__(self) -> None:
        if not (0 < self.max_turns <= 30):
            raise ValueError("Run limits allow at most 30 model turns")
        if self.max_tool_calls is not None and (type(self.max_tool_calls) is not int or self.max_tool_calls <= 0):
            raise ValueError("max_tool_calls must be a positive integer or None for unlimited")
        if not (0 < self.wall_time_seconds <= 1800 and 0 < self.tool_timeout_seconds <= 120):
            raise ValueError("Stage A wall-time and tool-time limits may not exceed 1800 and 120 seconds")
        if not (256 <= self.per_tool_output_bytes <= 16 * 1024 and
                256 <= self.total_model_output_bytes <= 256 * 1024):
            raise ValueError("Stage A model-bound output limits may not exceed 16 KiB per tool or 256 KiB total")


@dataclass(frozen=True, slots=True)
class RunResult:
    run_id: str
    status: str
    stop_reason: str
    turns: int
    tool_calls: int
    verified: bool
    run_dir: str
    usage_totals: dict[str, int | float]
    cleanup_errors: tuple[str, ...] = ()
    result_contract_version: int = 2
    candidate_ids: tuple[str, ...] = ()


class AgentLoop:
    def __init__(self, model: ModelSession, tools: ToolRegistry, evidence: EvidenceStore,
                 *, limits: RunLimits = RunLimits(), challenge_id: str = "unknown",
                 challenge_provenance: Mapping[str, Any] | None = None,
                 control: RunControl | None = None, run_id: str | None = None) -> None:
        self.model = model
        self.tools = tools
        self.evidence = evidence
        self.limits = limits
        self.challenge_id = challenge_id
        self.provenance = dict(challenge_provenance or {})
        self.control = control
        self.run_id = run_id or str(uuid.uuid4())
        self.turns = 0
        self.tool_calls = 0
        self.call_counts = {kind: {"requested": 0, "admitted": 0, "rejected": 0} for kind in ("execution", "observation", "control")}
        self.model_output_bytes = 0
        self.usage_totals: dict[str, int | float] = {}
        self.status = "unverified"
        self.stop_reason = "not_started"
        self._verified = False
        self._completed = False
        self._budget_stopped = False
        self.cleanup_errors: list[str] = []
        self.model_close_attempted = False
        self._sandbox_lost = False

    def run(self, task: str) -> RunResult:
        started = time.monotonic()
        deadline = started + self.limits.wall_time_seconds
        self.tools.run_deadline = deadline
        self.tools.reliability.context = {"run_id": self.run_id, "challenge_id": self.challenge_id,
            "runtime_image": getattr(self.tools.runtime, "image_digest", None),
            "input_provenance_sha256": hashlib.sha256(json.dumps(self.provenance, sort_keys=True).encode()).hexdigest()}
        self.tools.reliability.expected_inputs = {item['path']: item['sha256'] for item in self.provenance.get('input_files', [])
                                                 if isinstance(item, dict) and 'path' in item and 'sha256' in item}
        task_artifact = self.evidence.write_artifact(task.encode("utf-8"), media_type="text/markdown; charset=utf-8")
        tool_schema_payload = json.dumps(
            [spec.as_provider_schema() for spec in self.tools.specs],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self.evidence.append(
            "run_started",
            run_id=self.run_id,
            challenge_id=self.challenge_id,
            challenge_provenance=self.provenance,
            limits=asdict(self.limits),
            task_artifact=task_artifact,
            tool_schema_sha256=hashlib.sha256(tool_schema_payload).hexdigest(),
            tool_schema_artifact=self.evidence.write_artifact(tool_schema_payload, media_type='application/json'),
            oracle_access="controller_only" if self.tools.oracle_path else "not_provided",
            result_contract_version=2,
        )
        prompt = self._initial_prompt(task)
        try:
            while self.turns < self.limits.max_turns and not (self._verified or self._completed):
                if self._cancel_requested:
                    self._mark_user_cancelled("user_request")
                    break
                if self._runtime_closed:
                    self._mark_sandbox_lost()
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.stop_reason = "wall_time_exhausted"
                    self.status = "budget_exhausted"
                    break
                self.turns += 1
                self.evidence.append(
                    "model_turn_started",
                    turn=self.turns,
                    prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                    prompt_artifact=self.evidence.write_artifact(prompt.encode("utf-8"), media_type="text/plain; charset=utf-8"),
                )
                call_counter = 0

                def dispatch(call: ToolCall) -> ToolReply:
                    nonlocal call_counter
                    if self._sandbox_lost or (self._runtime_closed and not self._cancel_requested):
                        self._mark_sandbox_lost()
                        raise _SandboxUnavailable("sandbox already closed")
                    call_counter += 1
                    self.tool_calls += 1
                    category = ("control" if call.name in {"run_complete", "session_close"} else
                                "observation" if call.name in {"session_read", "observation_get", "artifact_read", "challenge_list", "challenge_read_text", "challenge_read_bytes", "experiment_record", "claim_record", "summary_update", "workflow_read"} else "execution")
                    self.call_counts[category]["requested"] += 1
                    admitted = False
                    raw_args = json.dumps(dict(call.arguments), ensure_ascii=False, sort_keys=True).encode("utf-8")
                    args_sha256 = hashlib.sha256(raw_args).hexdigest()
                    args_artifact = (
                        self.evidence.write_artifact(raw_args, media_type="application/json")
                        if len(raw_args) <= 16 * 1024
                        else None
                    )
                    call_record = {
                        "call_id": call.call_id,
                        "name": call.name,
                        "arguments_sha256": args_sha256,
                        "arguments_bytes": len(raw_args),
                        "arguments_artifact": args_artifact,
                        "tool_index": self.tool_calls,
                        "turn": self.turns,
                    }
                    self.evidence.append("tool_call", **call_record)
                    tool_started = time.monotonic()
                    allowed = 0
                    if self._verified:
                        outcome_status = "already_verified"
                        reply = ToolReply("A candidate has already been verified; no further tools will run.", True)
                        result = {"status": outcome_status}
                    elif self._completed:
                        reply = ToolReply("This attempt has explicitly completed; no further tools will run.", True)
                        result = {"status": "already_completed"}
                    elif self._cancel_requested:
                        outcome_status = "user_cancelled"
                        reply = ToolReply("The run has been stopped; no further tools will run.", False)
                        result = {"status": outcome_status}
                    elif len(raw_args) > 16 * 1024:
                        outcome_status = "invalid_arguments"
                        reply = ToolReply("Tool arguments exceed the 16 KiB request limit.", False)
                        result = {"status": outcome_status, "limit_bytes": 16 * 1024}
                    elif category != "control" and self.limits.max_tool_calls is not None and self.tool_calls > self.limits.max_tool_calls:
                        outcome_status = "budget_denied"
                        reply = ToolReply("Tool-call budget exhausted; no further tools can run.", False)
                        result: dict[str, Any] = {"status": outcome_status, "budget": "max_tool_calls"}
                        self._budget_stopped = True
                    elif category != "control" and time.monotonic() >= deadline:
                        outcome_status = "budget_denied"
                        reply = ToolReply("Run wall-time budget exhausted; no further tools can run.", False)
                        result = {"status": outcome_status, "budget": "wall_time_seconds"}
                        self._budget_stopped = True
                    elif category != "control" and self.model_output_bytes >= self.limits.total_model_output_bytes:
                        outcome_status = "budget_denied"
                        reply = ToolReply("Tool-output budget exhausted; no further tools can run.", False)
                        result = {"status": outcome_status, "budget": "total_model_output_bytes"}
                        self._budget_stopped = True
                    elif category != "control" and self.limits.total_model_output_bytes - self.model_output_bytes < 256:
                        outcome_status = "budget_denied"
                        reply = ToolReply("Less than 256 bytes remain in the tool-output budget; no further tools can run.", False)
                        result = {"status": outcome_status, "budget": "total_model_output_bytes"}
                        self._budget_stopped = True
                    else:
                        admitted = True
                        self.call_counts[category]["admitted"] += 1
                        self.tools.reliability.budget_feedback = {
                            "tool_calls": self.tool_calls, "tool_call_limit": self.limits.max_tool_calls,
                            "remaining_tool_calls": None if self.limits.max_tool_calls is None else max(0, self.limits.max_tool_calls-self.tool_calls),
                            "remaining_wall_seconds": round(max(0, deadline-time.monotonic()), 3),
                            "remaining_output_bytes": max(0, self.limits.total_model_output_bytes-self.model_output_bytes),
                            "counts": self.call_counts,
                        }
                        original_tool_timeout = self.tools.command_timeout
                        self.tools.command_timeout = min(
                            original_tool_timeout,
                            max(0.1, deadline - time.monotonic()),
                        )
                        try:
                            self.tools.reliability.call_context = {"turn": self.turns, "tool_index": self.tool_calls,
                                                                   "call_id": call.call_id, "public_message_count": public_messages}
                            outcome = self.tools.invoke(call)
                        finally:
                            self.tools.command_timeout = original_tool_timeout
                        reply = outcome.reply
                        result = outcome.result
                        result_payload = reply.content.encode("utf-8")
                        allowed = (self.limits.per_tool_output_bytes if category == "control" else
                                   min(self.limits.per_tool_output_bytes, self.limits.total_model_output_bytes - self.model_output_bytes))
                        if allowed < 0:
                            allowed = 0
                        if len(result_payload) > allowed:
                            artifact = self.evidence.write_artifact(result_payload, media_type="text/plain; charset=utf-8")
                            safe_text = _render_truncated_tool_reply(reply.content, artifact, allowed)
                            reply = ToolReply(safe_text, reply.success)
                            result = {**result, "presentation_truncated": True, "original_response_evidence": artifact}
                    if not admitted:
                        self.call_counts[category]["rejected"] += 1
                    result = {**result, "call_category": category, "admitted": admitted}
                    response_artifact = self.evidence.write_artifact(
                        reply.content.encode("utf-8"), media_type="text/plain; charset=utf-8"
                    )
                    self.model_output_bytes += min(len(reply.content.encode("utf-8")), allowed)
                    self.evidence.append(
                        "tool_result",
                        **call_record,
                        result=result,
                        response_evidence=response_artifact,
                        elapsed_seconds=round(time.monotonic() - tool_started, 3),
                    )
                    if self._runtime_closed and not self._cancel_requested:
                        self._mark_sandbox_lost()
                        raise _SandboxUnavailable("sandbox closed; stopping this run")
                    if call.name == "candidate_submit" and result.get("status") == "verified" and result.get("verification_method") == "exact-string controller-only":
                        self._verified = True
                        self.status = "verified"
                        self.stop_reason = "verified"
                    elif call.name == "run_complete" and result.get("status") == "run_complete":
                        self._completed = True
                        exhausted = (self._budget_stopped or time.monotonic() >= deadline or
                                     (self.limits.max_tool_calls is not None and self.tool_calls-1 >= self.limits.max_tool_calls))
                        self.status = "budget_exhausted" if exhausted else result["outcome"]
                        self.stop_reason = "budget_exhausted" if exhausted else "model_explicit_completion"
                        cancel = getattr(self.model, 'cancel', None)
                        if callable(cancel):
                            try:
                                cancel()
                            except Exception as exc:
                                self.evidence.append('provider_stop_error', kind=type(exc).__name__)
                    return reply

                public_messages = 0

                def publish_message(text: str) -> None:
                    nonlocal public_messages
                    if not isinstance(text, str) or not text.strip():
                        return
                    artifact = self.evidence.write_artifact(text.encode("utf-8"), media_type="text/plain; charset=utf-8")
                    self.evidence.append("assistant_message", turn=self.turns, evidence=artifact)
                    public_messages += 1

                try:
                    observe = getattr(self.model, "set_message_handler", None)
                    if callable(observe):
                        observe(publish_message)
                    turn = self.model.run_turn(
                        prompt,
                        self.tools.specs,
                        dispatch,
                        timeout=max(0.1, remaining),
                    )
                except TimeoutError:
                    if self._completed:
                        break
                    elif self._sandbox_lost or (self._runtime_closed and not self._cancel_requested):
                        self._mark_sandbox_lost()
                        break
                    elif self._cancel_requested and not self._verified:
                        self._mark_user_cancelled("user_request")
                    else:
                        self.stop_reason = "provider_timeout"
                        self.status = "provider_error"
                        self.evidence.append("provider_error", turn=self.turns, kind="timeout")
                    break
                except KeyboardInterrupt:
                    self._mark_user_cancelled("keyboard_interrupt")
                    break
                except Exception as exc:
                    if self._completed:
                        break
                    elif self._sandbox_lost or (self._runtime_closed and not self._cancel_requested):
                        self._mark_sandbox_lost()
                        break
                    elif self._cancel_requested and not self._verified:
                        self._mark_user_cancelled("user_request")
                    else:
                        self.stop_reason = "provider_error"
                        self.status = "provider_error"
                        self.evidence.append(
                            "provider_error",
                            turn=self.turns,
                            kind=type(exc).__name__,
                            message=redact_sensitive_text(str(exc)),
                        )
                    break

                if turn.text and not public_messages:
                    publish_message(turn.text)
                usage = normalize_usage(turn.usage)
                if usage:
                    for key, value in usage.items():
                        if isinstance(value, (int, float)) and not isinstance(value, bool):
                            self.usage_totals[key] = self.usage_totals.get(key, 0) + value
                self.evidence.append(
                    "model_turn_completed",
                    turn=self.turns,
                    response_id=turn.response_id,
                    finish_reason=turn.finish_reason,
                    usage=usage,
                    provider_tool_calls=turn.tool_calls,
                    text_sha256=hashlib.sha256(turn.text.encode("utf-8")).hexdigest(),
                )
                if self._verified or self._completed:
                    break
                if self._sandbox_lost:
                    break
                if self._cancel_requested:
                    self._mark_user_cancelled("user_request")
                    break
                if self._runtime_closed:
                    self._mark_sandbox_lost()
                    break
                if self._budget_stopped:
                    self.stop_reason = "budget_exhausted"
                    self.status = "budget_exhausted"
                    break
                if not call_counter:
                    self.stop_reason = "model_finished_without_tool_call"
                    self.status = "candidate_unverified" if self.tools.reliability.candidates else "unverified"
                    self.evidence.append("controller_decision", turn=self.turns, action="stop",
                                         reason=self.stop_reason, result_status=self.status)
                    break
                prompt = self._continuation_prompt()
                self.evidence.append("controller_decision", turn=self.turns, action="continue",
                                     reason="no_explicit_completion", candidate_count=len(self.tools.reliability.candidates))
            else:
                if self.turns >= self.limits.max_turns and not (self._verified or self._completed):
                    self.stop_reason = "max_turns_exhausted"
                    self.status = "budget_exhausted"
            if self._verified:
                self.status = "verified"
                self.stop_reason = "verified"
            elif self._cancel_requested and not self._completed and self.status != "user_cancelled":
                self._mark_user_cancelled("user_request")
            elif self.stop_reason in {"not_started", ""}:
                self.stop_reason = "completed_unverified"
        finally:
            self.model_close_attempted = True
            try:
                self.model.close()
            except Exception as exc:
                self.cleanup_errors.append(type(exc).__name__)
                self.evidence.append(
                    "provider_close_error",
                    kind=type(exc).__name__,
                    message=redact_sensitive_text(str(exc)),
                )
        self.evidence.append(
            "run_finished",
            run_id=self.run_id,
            status=self.status,
            stop_reason=self.stop_reason,
            turns=self.turns,
            tool_calls=self.tool_calls,
            call_counts=self.call_counts,
            elapsed_seconds=round(time.monotonic() - started, 3),
            result_contract_version=2,
            candidate_ids=list(self.tools.reliability.candidates),
        )
        return RunResult(
            run_id=self.run_id,
            status=self.status,
            stop_reason=self.stop_reason,
            turns=self.turns,
            tool_calls=self.tool_calls,
            verified=self._verified,
            run_dir=str(self.evidence.run_dir),
            usage_totals=dict(self.usage_totals),
            cleanup_errors=tuple(self.cleanup_errors),
            candidate_ids=tuple(self.tools.reliability.candidates),
        )

    def _initial_prompt(self, task: str) -> str:
        endpoint = self.tools.service_endpoint
        remote = getattr(self.tools.runtime, "remote_spec", None)
        if remote is not None:
            introduction = ("You are solving one explicitly authorized remote TCP challenge. "
                            "Use only registered tools. The command/session container has no network; "
                            f"only remote_tcp_exchange and http_request may reach {remote.endpoint} "
                            "within the controller grant. Redirects never expand scope. ")
        elif endpoint:
            introduction = (
                "You are solving one authorized local TCP service CTF challenge. "
                "Use only the registered tools; challenge files are read-only and command execution is isolated "
                f"and limited to /work. Only controller-approved TCP endpoint {endpoint} is in scope. "
                "Do not access host services, other runs, or public network targets. "
            )
        else:
            introduction = (
                "You are solving one authorized offline CTF artifact challenge. "
                "Use only the registered tools; challenge files are read-only and command execution is isolated, "
                "network-disabled, and limited to /work. "
            )
        verification = (
            "Identify flag candidates from the task, user hints and solving evidence. "
            "There is no required flag format or format check. Submit a candidate with candidate_submit "
            "when you judge it to be a flag; preserve its exact original content and prefix. "
            "A candidate is unverified unless the controller's known-answer verifier confirms it. "
        )
        return (
            introduction + "Use workflow_discover and workflow_read for domain guidance; classification never changes scope. "
            "Save solve scripts with script_save/script_run and extracted evidence with artifact_export. "
            f"Each command/script has a hard elapsed-time limit of {self.tools.command_timeout:g} seconds, "
            "including commands that keep printing; silence alone is not a blocking detector. "
            "For expensive scripts, print concise progress (completed/total, elapsed time, best result) "
            "every 5-10 seconds with flush=True or Python -u. Bound loops and subprocess timeouts. "
            "Split searches into resumable batches; save checkpoints/results under /work and exit voluntarily "
            "before the tool deadline, leaving a safety margin. Resume the next batch in a later tool call. "
            "Progress does not extend the deadline; command/script output is delivered when the tool returns. "
            "For scripts expected to exceed the command deadline, use script_start if available instead of script_run. "
            "It returns a background session_id immediately and is not subject to the 120s command limit. "
            "Use session_read with collect_seconds=30 (up to 60) to combine heartbeats and wait for completion; "
            "use wait_seconds up to 10 for immediate interaction. Inspect elapsed_seconds and output_idle_seconds. "
            "Decide whether to keep waiting or session_close based on progress, output silence, expected algorithm "
            "cost and remaining run/tool budget. Silence alone is not proof of a hang; explain decisions and avoid "
            "tight polling. Save progress in /work and stop stalled or unproductive scripts. "
            "Background scripts are still capped by the remaining run budget (at most 1800 seconds) and output limits. "
            "Use bounded session_start/session_read for tasks needing incremental observation, subject to "
            "their idle/total limits and the overall run budget. A normal command timeout preserves /work; "
            "reduce the batch size before retrying. Never assume a closed sandbox can recover its workdir. "
            "Submit any flag candidate with candidate_submit. "
            "Maintain concise public facts, hypotheses, checks and unknowns, without private reasoning. "
            "Use summary_update to publish a complete concise dashboard in the user's language (Chinese by default). "
            "Update once per meaningful progress step after important tool results or a new public explanation; "
            "prefer updating near your public explanation after inspecting results, before run_complete or final candidate submission. "
            "Include goal, up to 5 model-reported facts with observation IDs where available, up to 3 hypotheses, "
            "public approach and its basis, up to 2 next steps, blockers and corrections. "
            "Replace the whole dashboard; retract contradicted facts explicitly. Do not invent progress just to update it. "
            "Never turn an untested hypothesis into an established fact. A limited search failure only describes "
            "that search. Summary omissions do not prove absence in the original input; recheck focused raw evidence. "
            "Prefer cheap discriminating checks before expensive searches. Check available capabilities first; "
            "give each expensive library phase its own deadline/subprocess and checkpoint so one phase cannot stall all others. "
            "A command timeout is a terminal foreground result, not a running background task. Only a live session is background work. "
            "Tool requests are counted without a default count limit; remaining wall time and output limits are shown in run_budget. "
            "Use focused-read source_ref with parameter_key for experiment bindings instead of copying paths, offsets and hashes. "
            "Bind important experiment parameters to original input/artifact locations and hashes. Hardcoded guessed "
            "parameters are hypothesis experiments, not reproductions. Resolve source contradictions before strong claims. "
            "Exit 0, HTTP 200 and readable decoded text do not establish success. "
            "Use artifact_read/challenge_read_bytes for focused source checks (byte offsets, not virtual addresses). "
            "Use claim_record and experiment_record for important hypotheses/experiments, and candidate_check for "
            "applicable local relation checks. These tools validate limited provenance/relations, never arbitrary "
            "proofs. Reuse observation_get only within recorded image/input/run context; service/session observations "
            "are historical and may be stale. Recheck conditions after failures. "
            "Use run_complete(outcome='unsolved', summary=..., unresolved=[...]) when no justified next experiment remains. "
            "You may finish without any flag. If retaining an unverified candidate, use outcome='candidate_unverified' "
            "and its candidate_id. Do not guess a flag just to finish. "
            + verification + "\n\n"
            f"Challenge ID: {self.challenge_id}\n\nTask:\n{task}"
        )

    def _continuation_prompt(self) -> str:
        state = self.tools.reliability
        return ("No explicit completion was recorded. Review public facts, hypotheses, contradictions and unknowns. "
                "Continue only with a justified experiment that can distinguish a remaining hypothesis; inspect sources "
                "before repeating failed work. If no reasonable next step remains, call run_complete as unsolved; "
                "a flag is not required. You may explicitly finish with an unverified recorded candidate. "
                "Do not fabricate a candidate to satisfy the controller. Use summary_update after meaningful new results or a change of approach; avoid repeated updates without progress. "
                f"Recorded candidate IDs: {list(state.candidates)}; claim IDs: {list(state.claims)}.")

    @property
    def _cancel_requested(self) -> bool:
        return self.control is not None and self.control.cancelled

    @property
    def _runtime_closed(self) -> bool:
        return bool(getattr(self.tools.runtime, "closed", False))

    def _mark_sandbox_lost(self) -> None:
        if self._sandbox_lost:
            return
        self._sandbox_lost = True
        self.status = "error"
        self.stop_reason = "sandbox_closed"
        self.evidence.append("sandbox_error", turn=self.turns,
                             reason="sandbox_closed", tool_index=self.tool_calls)
        cancel = getattr(self.model, "cancel", None)
        if callable(cancel):
            try:
                cancel()
            except Exception:
                pass

    @property
    def verified(self) -> bool:
        return self._verified

    def _mark_user_cancelled(self, reason: str) -> None:
        if self._verified:
            return
        if self.status == "user_cancelled":
            return
        self.status = "user_cancelled"
        self.stop_reason = "user_cancelled"
        self.evidence.append("run_cancelled", turn=self.turns, reason=reason)


def _render_truncated_tool_reply(content: str, artifact: dict[str, Any], maximum: int) -> str:
    """Keep a clipped tool response valid JSON while linking the complete evidence."""
    preview = ""
    payload = {
        "truncated": True,
        "preview": preview,
        "full_response_artifact": artifact["artifact"],
        "full_response_sha256": artifact["sha256"],
    }
    rendered = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    low, high = 0, len(content)
    while low < high:
        middle = (low + high + 1) // 2
        payload["preview"] = content[:middle]
        candidate = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(candidate.encode("utf-8")) <= maximum:
            rendered = candidate
            low = middle
        else:
            high = middle - 1
    payload["preview"] = content[:low]
    rendered = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(rendered.encode("utf-8")) > maximum:
        return '{"truncated":true}'
    return rendered
