"""Durable, single-run lifecycle state for the local execution path."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ctfbot.evidence.store import EvidenceStore


_TERMINAL_STATES = {"completed", "failed", "cancelled", "timed_out"}
_ALLOWED_TRANSITIONS = {
    "preparing": {"starting", "failed", "cancelled", "timed_out"},
    "starting": {"starting", "running", "stopping", "failed", "cancelled", "timed_out"},
    "running": {"stopping", "completed", "failed", "cancelled", "timed_out"},
    "stopping": {"completed", "failed", "cancelled", "timed_out"},
}


class RunLifecycle:
    """Persist current run state and append each transition to the evidence timeline.

    Runs are intentionally not resumed or automatically retried. A new attempt
    always gets a new run ID, while a nonterminal state preserves the fact that
    the previous attempt stopped before its final transition.
    """

    def __init__(self, run_dir: Path, evidence: EvidenceStore, *, run_id: str, challenge_id: str) -> None:
        self.path = run_dir / "run-state.json"
        self.evidence = evidence
        now = _now()
        self._state: dict[str, Any] = {
            "schema_version": 1,
            "run_id": run_id,
            "challenge_id": challenge_id,
            "state": "preparing",
            "phase": "preflight_complete",
            "result_status": None,
            "stop_reason": None,
            "failure_class": None,
            "cleanup_status": "pending",
            "created_at_utc": now,
            "updated_at_utc": now,
            "finished_at_utc": None,
            "recovery": {
                "automatic_retry": False,
                "resume_supported": False,
                "action": "review_partial_evidence_then_start_new_run",
                "parent_run_id": None,
            },
        }
        self._persist()
        self._record(None)

    @property
    def state(self) -> dict[str, Any]:
        return dict(self._state)

    @property
    def terminal(self) -> bool:
        return self._state["state"] in _TERMINAL_STATES

    def transition(
        self,
        state: str,
        *,
        phase: str,
        result_status: str | None = None,
        stop_reason: str | None = None,
        failure_class: str | None = None,
        cleanup_status: str | None = None,
    ) -> None:
        previous = str(self._state["state"])
        if self.terminal or state not in _ALLOWED_TRANSITIONS.get(previous, set()):
            raise RuntimeError(f"invalid run lifecycle transition: {previous} -> {state}")
        now = _now()
        self._state.update({
            "state": state,
            "phase": phase,
            "result_status": result_status,
            "stop_reason": stop_reason,
            "failure_class": failure_class,
            "updated_at_utc": now,
        })
        if cleanup_status is not None:
            self._state["cleanup_status"] = cleanup_status
        if state in _TERMINAL_STATES:
            self._state["finished_at_utc"] = now
            if cleanup_status is None:
                self._state["cleanup_status"] = "complete"
        self._persist()
        self._record(previous)

    def _persist(self) -> None:
        fd, temporary = tempfile.mkstemp(prefix=".run-state-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(self._state, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def _record(self, previous: str | None) -> None:
        self.evidence.append(
            "run_state_changed",
            previous_state=previous,
            state=self._state["state"],
            phase=self._state["phase"],
            result_status=self._state["result_status"],
            stop_reason=self._state["stop_reason"],
            failure_class=self._state["failure_class"],
            cleanup_status=self._state["cleanup_status"],
            recovery=self._state["recovery"],
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
