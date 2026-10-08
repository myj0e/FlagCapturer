"""Bounded views of controller-owned state for the current, still-live run.

Records stay with their domain owners. This index never turns a public model
summary into proof, and its persisted artifacts are audit records, not resume
instructions for another process.
"""
from __future__ import annotations

import base64
import json
from typing import Any

SECTIONS = ("overview", "summary", "observations", "claims", "experiments", "candidates",
            "scripts", "sessions", "checkpoints")
RECOVERY_TOOLS = frozenset({"state_read", "observation_get", "artifact_read", "artifact_tail", "artifact_find"})
MAX_CONTEXT_BYTES = 8192


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class RunContextState:
    schema_version = 1

    def __init__(self, registry):
        self.registry = registry
        self.revision = 0
        self._sessions_signature = ""

    @property
    def run_id(self) -> str:
        return self.registry.reliability.context.get("run_id", self.registry.evidence.run_dir.name)

    def records(self, section: str) -> dict:
        state = self.registry.reliability
        if section == "summary":
            return state.summaries
        if section in {"observations", "claims", "experiments", "candidates"}:
            return getattr(state, section)
        if section in {"scripts", "checkpoints"}:
            return getattr(self.registry, section)
        if section == "sessions":
            with self.registry._session_lock:
                return {key: {**metadata, **transcript.snapshot()}
                        for key, (metadata, transcript) in self.registry.session_records.items()}
        raise ValueError("unknown state section")

    def changed(self) -> None:
        self.revision += 1
        # Persist references/IDs; domain events retain full records and source bytes.
        index = {"schema_version": self.schema_version, "run_id": self.run_id,
                 "revision": self.revision, "resume_supported": False,
                 "sections": {section: list(self.records(section)) for section in SECTIONS[1:]}}
        artifact = self.registry.evidence.write_artifact(encode(index).encode(), media_type="application/json")
        self.registry.evidence.append("context_index_updated", revision=self.revision, index=artifact)

    def refresh_sessions(self) -> None:
        signature = encode(self.records("sessions"))
        if signature != self._sessions_signature:
            self._sessions_signature = signature
            if signature != "{}":
                self.changed()

    def overview(self) -> dict:
        return {"context": dict(self.registry.reliability.context), "resume_supported": False,
                "counts": {section: len(self.records(section)) for section in SECTIONS[1:]},
                "latest_summary_revision": self.registry.reliability.summary.get("revision")
                if self.registry.reliability.summary else None,
                "budget": self.registry.reliability.budget_feedback,
                "provider_context": self.registry.provider_context}

    def _cursor(self, section: str, offset: int) -> str:
        data = encode([self.run_id, section, self.revision, offset]).encode()
        return base64.urlsafe_b64encode(data).decode()

    def _offset(self, token: str, section: str, size: int) -> int:
        try:
            run, cursor_section, revision, offset = json.loads(base64.b64decode(token, altchars=b'-_', validate=True))
        except (ValueError, TypeError):
            raise ValueError("invalid state cursor") from None
        if not isinstance(run, str) or not isinstance(cursor_section, str) or type(revision) is not int:
            raise ValueError("invalid state cursor fields")
        if run != self.run_id or cursor_section != section:
            raise ValueError("cursor belongs to another run or section")
        if revision != self.revision:
            raise ValueError("stale state cursor; restart pagination")
        if type(offset) is not int or not 0 <= offset < size:
            raise ValueError("cursor offset out of range")
        return offset

    def _view(self, key: str, record: dict) -> dict:
        sources = {}
        def visit(value):
            if isinstance(value, dict):
                for item in value.values():
                    visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)
            elif isinstance(value, str) and value.startswith("artifacts/sha256-"):
                sources[value] = self.registry.evidence.reference_status(value)
        visit(record)
        view = {"id": key, **record, "source_status": sources,
                "historical_record": True}
        # Full records remain available even if unusually large metadata is omitted.
        if len(encode(view).encode()) > 3000:
            source = self.registry.evidence.write_artifact(encode(record).encode(), media_type="application/json")
            missing = {path: status for path, status in sources.items() if status != "present_hash_unchecked"}
            view = {"id": key, "record_artifact": source, "omitted_fields": list(record),
                    "source_status": dict(list(missing.items())[:16]), "additional_source_issues": max(0, len(missing)-16),
                    **{k: record[k] for k in ("status", "tool", "state", "revision") if k in record}}
        return view

    def read(self, args: dict) -> dict:
        self.refresh_sessions()
        section = args["section"]
        envelope = {"status": "ok", "schema_version": self.schema_version, "run_id": self.run_id,
                    "revision": self.revision, "section": section}
        if section == "overview":
            if args.get("id") or args.get("cursor"):
                raise ValueError("overview has no record ID or cursor")
            return {**envelope, **self.overview()}
        records = self.records(section)
        keys = list(records)
        if "id" in args:
            if "cursor" in args:
                raise ValueError("choose a record ID or cursor")
            key = args["id"]
            if section == "summary" and key == "latest":
                key = keys[-1] if keys else "latest"
            if key not in records:
                raise ValueError("record missing from this run")
            return {**envelope, "records": [self._view(key, records[key])], "next_cursor": None}
        start = self._offset(args["cursor"], section, len(keys)) if "cursor" in args else 0
        result = {**envelope, "records": [], "total": len(keys), "next_cursor": None}
        offset = start
        for key in keys[start:start + args.get("limit", 20)]:
            proposed = {**result, "records": [*result["records"], self._view(key, records[key])],
                        "next_cursor": self._cursor(section, offset + 1) if offset + 1 < len(keys) else None}
            if len(encode(proposed).encode()) > MAX_CONTEXT_BYTES:
                break
            result = proposed
            offset += 1
        result["next_cursor"] = self._cursor(section, offset) if offset < len(keys) else None
        return result

    def snapshot(self, *, reason: str, constraints: dict) -> str:
        self.refresh_sessions()
        state = self.registry.reliability
        payload = {"schema_version": self.schema_version, "run_id": self.run_id,
                   "revision": self.revision, "reason": reason, "constraints": constraints,
                   "model_reported_summary": state.summary,
                   "summary_is_controller_proof": False, "budget": state.budget_feedback,
                   "provider_context": self.registry.provider_context,
                   "resume_supported": False, "retrieve": "state_read(section, id or cursor); artifact_read",
                   "omitted_sections": []}
        for section in ("sessions", "scripts", "candidates", "experiments", "claims", "observations", "checkpoints"):
            records = self.records(section)
            keys = list(records)
            selected = keys[-3:]
            views = [self._view(key, records[key]) for key in selected]
            trial = {**payload, section: views}
            if len(encode(trial).encode()) <= MAX_CONTEXT_BYTES - 512:
                payload = trial
            else:
                selected = []
            if len(selected) < len(keys):
                payload["omitted_sections"].append(section)
        text = encode(payload)
        if len(text.encode()) > MAX_CONTEXT_BYTES:
            # Summary has a bounded schema, but Unicode can exceed the byte ceiling.
            payload["model_reported_summary"] = {"revision": state.summary["revision"], "read": "state_read summary latest"} if state.summary else None
            text = encode(payload)
        artifact = self.registry.evidence.write_artifact(text.encode(), media_type="application/json")
        self.registry.evidence.append("context_snapshot", reason=reason, revision=self.revision,
                                      bytes=len(text.encode()), snapshot=artifact)
        return text
