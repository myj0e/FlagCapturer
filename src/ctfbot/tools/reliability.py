"""Versioned run-local provenance, public claims, checks and completion tools."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import PurePosixPath
from typing import Any

from ctfbot.model_adapters.protocol import ToolReply, ToolSpec
from ctfbot.tools.registry import ToolOutcome
from ctfbot.evidence.store import EvidenceIntegrityError


CONTRACT_VERSION = 2


def string(maximum=2048, **extra):
    return {"type": "string", "maxLength": maximum, **extra}


def refs():
    return {"type": "array", "items": string(80), "maxItems": 32}


def text_list():
    return {"type": "array", "items": string(), "maxItems": 16}


SUMMARY_PROPERTIES = {
    "goal": string(240),
    "facts": {"type": "array", "maxItems": 5, "items": {
        "type": "object", "properties": {"text": string(320), "observation_ids": refs()},
        "required": ["text", "observation_ids"], "additionalProperties": False}},
    "hypotheses": {"type": "array", "items": string(320), "maxItems": 3},
    "approach": string(600),
    "next_steps": {"type": "array", "items": string(240), "maxItems": 2},
    "blockers": {"type": "array", "items": string(240), "maxItems": 3},
    "corrections": {"type": "array", "items": string(320), "maxItems": 3},
}
SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {**SUMMARY_PROPERTIES, "turn": {"type": "integer", "minimum": 0},
                   "revision": {"type": "integer", "minimum": 1}, "controller_proven": {"type": "boolean"}},
    "required": [*SUMMARY_PROPERTIES, "turn", "revision", "controller_proven"],
    "additionalProperties": False,
}


class ReliabilityTools:
    def __init__(self, registry):
        self.registry = registry
        self.candidates: dict[str, dict[str, Any]] = {}
        self.observations: dict[str, dict[str, Any]] = {}
        self.claims: dict[str, dict[str, Any]] = {}
        self.experiments: dict[str, dict[str, Any]] = {}
        self.checks: dict[str, dict[str, Any]] = {}
        self.completion: dict[str, Any] | None = None
        self.context: dict[str, Any] = {}
        self.expected_inputs: dict[str, str] = {}
        self.call_context: dict[str, Any] = {}
        self.budget_feedback: dict[str, Any] = {}
        self.source_refs: dict[str, dict[str, Any]] = {}
        self.summary: dict[str, Any] | None = None
        self.summaries: dict[str, dict[str, Any]] = {}
        self._summary_progress: tuple[int, int, int] | None = None
        self._progress_revision = 0
        self._install()

    def define(self, name, description, properties, required, handler):
        self.registry._definitions[name] = (ToolSpec(name, description, {
            "type": "object", "properties": properties, "required": required,
            "additionalProperties": False,
        }), handler)

    def reply(self, payload, success=True):
        return ToolOutcome(ToolReply(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), success), payload)

    def present_recovery(self, outcome):
        """Audit a retrieval without creating observations or changing its cursor."""
        from ctfbot.tools.presentation import render
        try:
            payload = json.loads(outcome.reply.content)
        except ValueError:
            payload = {"message": outcome.reply.content}
        payload = {**outcome.result, **payload}
        raw = self.registry.evidence.write_artifact(json.dumps(payload, ensure_ascii=False).encode(), media_type="application/json")
        rendered = render(payload, 8192, artifact=raw)
        return ToolOutcome(ToolReply(rendered, outcome.reply.success),
                           {**outcome.result, "raw_response_evidence": raw, "presentation_source_evidence": raw})

    def selected(self, values, records):
        if not isinstance(values, list) or len(values) > 32 or any(not isinstance(v, str) or v not in records for v in values):
            raise ValueError("references must identify existing records owned by this run")
        return values

    def _install(self):
        from ctfbot.agent.context import SECTIONS
        self.define("state_read", "Read controller-owned current-run state in bounded pages. Summaries are model-reported, sessions are historical/as-of observations; enumeration never consumes output. Cursor expires on state revision changes. Use summary id=latest or its revision. Missing IDs are errors; omitted data is not absent.", {
            "section": string(enum=list(SECTIONS)), "id": string(512), "cursor": string(2048),
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        }, ["section"], lambda args: self.reply(self.registry.context_state.read(args)))
        def submit(args):
            sources = self.selected(args.get("observation_ids", []), self.observations)
            checks = self.selected(args.get("check_ids", []), self.checks)
            # A check of another candidate cannot be transferred to this one.
            digest = hashlib.sha256(args["candidate"].encode()).hexdigest()
            if any(self.checks[c]["candidate_sha256"] != digest for c in checks):
                raise ValueError("check belongs to different candidate bytes")
            cid = f"candidate-{len(self.candidates)+1}"
            record = {**self.registry.record_candidate(args["candidate"]), "contract_version": CONTRACT_VERSION,
                      "candidate_id": cid, "candidate_sha256": digest,
                      "observation_ids": sources, "check_ids": checks,
                      "derivation": args.get("derivation", ""),
                      "unchecked_reason": args.get("unchecked_reason", "No provenance/check supplied."),
                      "verified": False}
            self.candidates[cid] = record
            details = self.registry.evidence.write_artifact(json.dumps(record).encode(), media_type="application/json")
            self.registry.evidence.append("candidate_recorded", candidate_id=cid, status=record["status"],
                                          verified=record["verified"], details=details)
            payload = {key: value for key, value in record.items()
                       if key not in {"candidate_evidence", "derivation"}}
            payload["ends_run"] = False
            return ToolOutcome(ToolReply(json.dumps(payload)), {**record, "ends_run": False})

        self.define("candidate_submit", "Record a candidate and its provenance; any model-selected flag is accepted without a format check; submission does NOT prove correctness or end the run. Supply observations, derivation and checks, or unchecked_reason. Use run_complete to explicitly finish with an unverified candidate.", {
            "candidate": string(4096), "observation_ids": refs(), "check_ids": refs(),
            "derivation": string(), "unchecked_reason": string(),
        }, ["candidate"], submit)
        self.define("summary_update", "Replace the complete PUBLIC solving dashboard after important results or a change of direction, once per meaningful progress step; repeated updates need new public explanation or tool results. Keep facts separate from hypotheses. Facts are model-reported, never controller proof; attach observation_ids when available. Explicitly list corrections when retracting earlier conclusions. Use concise text in the user's language; never provide private reasoning. This only updates display/evidence, not verification or completion.", SUMMARY_PROPERTIES, ["goal", "facts", "hypotheses", "approach", "next_steps", "blockers", "corrections"], self.update_summary)
        self.define("run_complete", "Explicitly end this attempt as unsolved or candidate_unverified. No flag is required for unsolved. Explain remaining unknowns; this is the model's conclusion, not proof of impossibility. A candidate must reference a candidate_id from this run.", {
            "outcome": string(enum=["unsolved", "candidate_unverified"]),
            "summary": string(), "unresolved": text_list(), "candidate_id": string(80),
            "observation_ids": refs(),
        }, ["outcome", "summary", "unresolved"], self.complete)
        self.define("artifact_read", "Read a bounded byte range from this run's hash-verified evidence, including saved full tool output. Offsets/lengths are BYTES; encoding utf8 replaces partial UTF-8 boundaries, hex is exact. A summary omission is not source absence.", {
            "artifact": string(100), "offset": {"type": "integer", "minimum": 0},
            "length": {"type": "integer", "minimum": 1, "maximum": 4096},
            "encoding": string(enum=["utf8", "hex"]),
        }, ["artifact", "offset", "length", "encoding"], self.read_artifact)
        self.define("artifact_tail", "Read the final bounded bytes of this run's hash-verified artifact. UTF-8 boundary replacement or exact hex; does not execute commands.", {
            "artifact": string(100), "length": {"type": "integer", "minimum": 1, "maximum": 4096},
            "encoding": string(enum=["utf8", "hex"]),
        }, ["artifact", "length", "encoding"], self.tail_artifact)
        self.define("artifact_find", "Locate literal UTF-8 bytes in a hash-verified run artifact. Scan at most 1 MiB from offset, return at most 20 byte positions, total matches within scan, and explicit partial-scan coverage. No regex.", {
            "artifact": string(100), "literal": string(256, minLength=1),
            "offset": {"type": "integer", "minimum": 0},
            "scan_bytes": {"type": "integer", "minimum": 1, "maximum": 1048576},
        }, ["artifact", "literal", "offset", "scan_bytes"], self.find_artifact)
        self.define("challenge_read_bytes", "Read a bounded byte range of an authorized input. Offsets/lengths are file BYTES, never virtual addresses. Returns full-input hash, coverage and exact hex; use domain tools for semantic locators.", {
            "path": string(512), "offset": {"type": "integer", "minimum": 0},
            "length": {"type": "integer", "minimum": 1, "maximum": 4096},
        }, ["path", "offset", "length"], self.read_input)
        self.define("observation_get", "Retrieve a prior observation's provenance and raw artifact references. This is historical evidence bound to the recorded run/image/input context, not a fresh service response. Failed or changing observations must be rechecked.", {
            "observation_id": string(80),
        }, ["observation_id"], self.get_observation)
        binding = {"type": "object", "properties": {
            "source_ref": string(80), "parameter_key": string(80),
            "observation_id": string(80), "artifact_path": string(100),
            "offset": {"type": "integer", "minimum": 0},
            "length": {"type": "integer", "minimum": 1, "maximum": 4096},
            "sha256": string(64, pattern="[0-9a-f]{64}"),
            "locator": string(512),
            "parameter": string(80), "representation": string(enum=['utf8','hex']),
        }, "additionalProperties": False}
        self.define("experiment_record", "Record an experiment, actual execution observation, parameters, transformations and bounded source byte/hash bindings. Prefer bindings=[{source_ref: <focused read source_ref>, parameter_key: <key in parameters>, representation: utf8|hex}]; referenced observations are included automatically. Legacy manual bindings and parameter remain supported. A binding's optional parameter + representation=utf8/hex checks a literal parameter against selected bytes; omit for transformed/semantic relations and document that limitation. Controller checks provenance/copy consistency only, never arbitrary proofs. Exit 0 or a self-reported result cannot establish a hypothesis.", {
            "execution_observation_id": string(80), "observation_ids": refs(),
            "parameters": {"type": "object", "maxProperties": 32},
            "transformations": text_list(), "purpose": string(),
            "bindings": {"type": "array", "items": binding, "maxItems": 16},
            "reported_result": string(enum=["unknown", "supports", "contradicts"]),
        }, ["execution_observation_id", "observation_ids", "parameters", "transformations", "purpose", "bindings", "reported_result"], self.record_experiment)
        self.define("claim_record", "Maintain concise PUBLIC facts/hypotheses/check results/unknowns; do not supply private reasoning. supported means model-reported support, never controller proof. Source-inconsistent experiments cannot upgrade a claim; use contradicted to retain contradictions and recheck original evidence.", {
            "statement": string(), "state": string(enum=["hypothesis", "supported", "contradicted"]),
            "observation_ids": refs(), "experiment_ids": refs(), "unknowns": text_list(),
        }, ["statement", "state", "observation_ids", "experiment_ids", "unknowns"], self.record_claim)
        self.define("candidate_check", "Run a versioned, source-hash-bound LOCAL relation check in the sandbox against an authorized input. A pass is local_check, NOT trusted verification or automatic completion. json_field_equals uses an RFC6901 JSON pointer. http_body_equals compares the raw body, never status 200 alone. Input must be <=1 MiB.", {
            "candidate_id": string(80), "operation": string(enum=["base64_equals", "json_field_equals", "http_body_equals"]),
            "path": string(512), "selector": string(512),
        }, ["candidate_id", "operation", "path"], self.check_candidate)

    def update_summary(self, args):
        turn = self.call_context.get("turn", 0)
        # The loop supplies a run-wide public-message revision. A new turn or
        # recovery reads alone do not constitute meaningful solving progress.
        public_revision = self.call_context.get("public_message_revision")
        progress = ((0, public_revision, self._progress_revision) if public_revision is not None else
                    (turn, self.call_context.get("public_message_count", 0), self._progress_revision))
        if self._summary_progress == progress:
            return self.reply({"status": "summary_already_updated", "turn": turn,
                               "message": "No new public explanation or tool results since the last update; keep the dashboard."})
        for fact in args["facts"]:
            self.selected(fact["observation_ids"], self.observations)
        revision = self.summary["revision"] + 1 if self.summary else 1
        record = {**args, "turn": turn, "revision": revision, "controller_proven": False}
        details = self.registry.evidence.write_artifact(
            json.dumps(record, ensure_ascii=False).encode(), media_type="application/json")
        self.registry.evidence.append("summary_updated", turn=turn, revision=revision, details=details)
        self.summary = record
        self.summaries[str(revision)] = {**record, "source": details}
        self._summary_progress = progress
        return self.reply({"status": "summary_updated", "turn": turn, "revision": revision})

    def observe(self, call, outcome):
        """Keep raw reply and exact provenance even when model presentation is clipped."""
        from ctfbot.agent.context import RECOVERY_TOOLS
        if call.name not in RECOVERY_TOOLS | {"summary_update", "claim_record", "experiment_record", "run_complete"}:
            self._progress_revision += 1
        raw = self.registry.evidence.write_artifact(outcome.reply.content.encode(), media_type="text/plain; charset=utf-8")
        try:
            payload = json.loads(outcome.reply.content)
        except ValueError:
            payload = {"message": outcome.reply.content}
        if not isinstance(payload, dict): payload = {"content": payload}
        for key in ("status", "error_kind", "error_evidence", "next_step"):
            if key in outcome.result:
                payload.setdefault(key, outcome.result[key])
        oid = f"observation-{len(self.observations)+1}"
        sources = {}
        def visit(value):
            if isinstance(value, dict):
                if all(key in value for key in ("artifact", "sha256", "bytes")):
                    sources[value['artifact']] = value
                for item in value.values():
                    visit(item)
            elif isinstance(value, list):
                for item in value: visit(item)
        visit(outcome.result)
        visit(payload)
        sources[raw['artifact']] = raw
        if call.name in {'challenge_read_bytes', 'artifact_read', 'artifact_tail'} and outcome.reply.success:
            ref_id = f"source-{len(self.source_refs)+1}"
            if call.name == 'challenge_read_bytes':
                source = outcome.result['evidence']
                offset = 0
                length = source['bytes']
                digest = source['sha256']
            else:
                source = outcome.result['source']
                offset = outcome.result['coverage']['offset']
                length = outcome.result['coverage']['length']
                digest = outcome.result['selected_sha256']
            self.source_refs[ref_id] = {"observation_id": oid, "artifact_path": source['artifact'],
                                       "offset": offset, "length": length, "sha256": digest}
            payload['source_ref'] = ref_id
            self.registry.evidence.append('source_reference_recorded', source_ref=ref_id,
                                          binding=self.source_refs[ref_id], context=dict(self.context))
            payload['source_coordinates'] = {"artifact_offset": offset, "length": length,
                                              "input_offset": call.arguments.get('offset') if call.name == 'challenge_read_bytes' else None}
        source_kind = 'session' if call.name.startswith('session_') else 'request' if call.name in {'http_request','remote_tcp_exchange'} else 'tool'
        coverage = outcome.result.get("coverage", payload.get("coverage", {"source_kind": source_kind, "filter": "tool-defined; inspect raw evidence",
                                                   "truncated": bool(outcome.result.get("truncated")),
                                                   "completeness": "not asserted"}))
        if source_kind == 'session' and isinstance(call.arguments, dict):
            coverage = {**coverage, 'session_id': call.arguments.get('session_id',outcome.result.get('session_id'))}
        record = {"observation_id": oid, "tool": call.name, **self.call_context,
                  "status": outcome.result.get("status"), "raw_response": raw, "sources": list(sources.values()),
                  "coverage": coverage, "context": dict(self.context),
                  "historical": call.name in {"session_read", "remote_tcp_exchange", "http_request"}}
        if (call.name in {"script_run", "script_start"} and isinstance(call.arguments, dict)
                and isinstance(call.arguments.get("path"), str)
                and ("stdout_evidence" in outcome.result or "session_id" in outcome.result)):
            script = self.registry.scripts.get(PurePosixPath(call.arguments.get("path", "")).as_posix())
            if script is not None and script.get("last_execution") is not None:
                script["last_execution"]["observation_id"] = oid
                script["last_execution"]["raw_response"] = raw
        self.observations[oid] = record
        self.registry.evidence.append("observation_recorded", **record)
        payload["observation"] = {"id": oid, "raw": raw["artifact"], "coverage": coverage,
                                  "read": "artifact_read; offsets/lengths are bytes"}
        if self.budget_feedback:
            payload['run_budget'] = self.budget_feedback
            if self.budget_feedback.get('transmission', {}).get('ordinary_warning'):
                payload['output_warning'] = 'ordinary_pool_80_percent'
        from ctfbot.tools.presentation import render
        full_presentation = self.registry.evidence.write_artifact(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(), media_type="application/json")
        rendered = render(payload, self.registry.max_model_output_bytes, artifact=raw)
        presentation_truncated = len(json.dumps(payload).encode()) > self.registry.max_model_output_bytes
        result = {**outcome.result, "observation_id": oid, "raw_response_evidence": raw,
                  "presentation_truncated": presentation_truncated, "presentation_source_evidence": full_presentation}
        return ToolOutcome(ToolReply(rendered, outcome.reply.success), result)

    def read_artifact(self, args):
        data, source = self.registry.evidence.read_artifact(args['artifact'])
        selected = data[args['offset']:args['offset']+args['length']]
        coverage = {"source_kind": "artifact", "offset": args['offset'], "length": len(selected),
                    "source_bytes": len(data), "filter": "byte range", "truncated": len(selected) != len(data)}
        return self.reply({"status": "ok", "data": selected.hex() if args['encoding']=='hex' else selected.decode('utf-8',errors='replace'),
                           "source": source, "coverage": coverage,
                           "selected_sha256": hashlib.sha256(selected).hexdigest()})

    def tail_artifact(self, args):
        data, _ = self.registry.evidence.read_artifact(args['artifact'])
        return self.read_artifact({**args, "offset": max(0, len(data) - args['length'])})

    def find_artifact(self, args):
        data, source = self.registry.evidence.read_artifact(args['artifact'])
        start = args['offset']
        stop = min(len(data), start + args['scan_bytes'])
        needle = args['literal'].encode('utf-8')
        offsets, count, position = [], 0, start
        while position <= stop - len(needle):
            position = data.find(needle, position, stop)
            if position < 0:
                break
            count += 1
            if len(offsets) < 20:
                offsets.append(position)
            position += 1
        return self.reply({"status": "ok", "offsets": offsets, "matches_in_scan": count,
                           "matches_omitted": count - len(offsets), "source": source,
                           "coverage": {"offset": start, "end": stop, "source_bytes": len(data),
                                        "partial_scan": start > 0 or stop < len(data)}})

    def input_bytes(self, relative, maximum=32*1024*1024):
        target = self.registry._checked_file(self.registry.challenge_root, relative)
        if not target.is_file() or target.stat().st_size > maximum:
            raise ValueError(f"input must be a regular file of at most {maximum} bytes")
        data = target.read_bytes()
        if len(data) > maximum: raise ValueError("input grew beyond the read cap")
        expected = self.expected_inputs.get(PurePosixPath(relative).as_posix())
        if expected is not None and hashlib.sha256(data).hexdigest() != expected:
            raise EvidenceIntegrityError('input hash differs from the admitted run snapshot')
        return data

    def read_input(self, args):
        data = self.input_bytes(args['path'])
        selected = data[args['offset']:args['offset']+args['length']]
        source = self.registry.evidence.write_artifact(selected)
        return self.reply({"status": "ok", "data_hex": selected.hex(), "evidence": source,
                           "coverage": {"source_kind": "file", "path": args['path'],
                                        "input_sha256": hashlib.sha256(data).hexdigest(),
                                        "source_bytes": len(data), "offset": args['offset'], "length": len(selected),
                                        "filter": "byte range", "truncated": len(selected) != len(data)}})

    def get_observation(self, args):
        self.selected([args['observation_id']], self.observations)
        return self.reply({"status": "ok", "observation_record": self.observations[args['observation_id']],
                           "reuse": "historical only; never assume changing service state is current"})

    def record_experiment(self, args):
        ids = self.selected(args['observation_ids'], self.observations)
        execution = args['execution_observation_id']
        self.selected([execution], self.observations)
        if self.observations[execution]['tool'] not in {'command_run','script_run','script_start','workflow_run','session_read','http_request','remote_tcp_exchange'}:
            raise ValueError("execution_observation_id must refer to an actual execution/request observation")
        checks = []
        for index, original_binding in enumerate(args['bindings']):
            binding = dict(original_binding)
            if 'source_ref' in binding:
                ref = self.source_refs.get(binding['source_ref'])
                if ref is None:
                    raise ValueError(f'bindings[{index}].source_ref must be a source_ref returned by a focused read in this run')
                if any(key in binding and binding[key] != value for key,value in ref.items()):
                    raise ValueError(f'bindings[{index}] overrides immutable source_ref coordinates')
                binding.update(ref)
                if binding['observation_id'] not in ids:
                    ids = [*ids, binding['observation_id']]
            missing = [k for k in ('observation_id','artifact_path','offset','length','sha256') if k not in binding]
            if missing:
                example = next(iter(self.source_refs), '<source_ref returned by challenge_read_bytes>')
                raise ValueError(f'bindings[{index}] missing {missing}; use {{"source_ref":"{example}","parameter_key":"<key in parameters>","representation":"utf8"}}')
            if 'parameter_key' in binding:
                if 'parameter' in binding and binding['parameter'] != binding['parameter_key']:
                    raise ValueError(f'bindings[{index}].parameter_key conflicts with legacy parameter')
                binding['parameter'] = binding['parameter_key']
            self.selected([binding['observation_id']], self.observations)
            if binding['observation_id'] not in ids:
                raise ValueError("source binding is absent from observation_ids")
            allowed = {s['artifact'] for s in self.observations[binding['observation_id']]['sources']}
            if binding['artifact_path'] not in allowed:
                raise ValueError("artifact does not belong to the referenced observation")
            data, ref = self.registry.evidence.read_artifact(binding['artifact_path'])
            part = data[binding['offset']:binding['offset']+binding['length']]
            source_matches = len(part)==binding['length'] and hashlib.sha256(part).hexdigest()==binding['sha256']
            parameter_matches = None
            if 'parameter' in binding:
                if binding['parameter'] not in args['parameters']:
                    raise ValueError(f'bindings[{index}].parameter_key must name a key in parameters, not its value; available keys: {list(args["parameters"])}')
                value = args['parameters'][binding['parameter']]
                parameter_matches = (isinstance(value, (str,int)) and not isinstance(value,bool)
                                     and (str(value).lower()==part.hex() if binding.get('representation')=='hex'
                                          else str(value).encode()==part))
            passed = source_matches and parameter_matches is not False
            checks.append({**binding, "passed": passed, "source": ref,
                           "source_hash_matches": source_matches, "parameter_matches": parameter_matches})
        eid = f"experiment-{len(self.experiments)+1}"
        record = {**args, 'observation_ids': ids, 'experiment_id': eid, 'source_checks': checks,
                  'source_consistent': bool(checks) and all(c['passed'] for c in checks),
                  'verified': False, 'semantic_result': 'model_reported_only'}
        record['status'] = 'source_bound' if record['source_consistent'] else 'hypothesis_experiment'
        self.experiments[eid] = record
        detail = self.registry.evidence.write_artifact(json.dumps(record).encode(), media_type='application/json')
        self.registry.evidence.append('experiment_recorded', experiment_id=eid, details=detail,
                                      source_consistent=record['source_consistent'], reported_result=args['reported_result'])
        return self.reply({'status': record['status'], 'experiment_id': eid, 'source_checks': checks,
                           'source_consistent': record['source_consistent'], 'verified': False})

    def record_claim(self, args):
        self.selected(args['observation_ids'], self.observations)
        self.selected(args['experiment_ids'], self.experiments)
        requested, state = args['state'], args['state']
        issue = None
        if state == 'supported' and (not args['observation_ids'] or any(
            not self.experiments[e]['source_consistent'] for e in args['experiment_ids'])):
            state, issue = 'hypothesis', 'support_sources_missing_or_inconsistent'
        cid = f"claim-{len(self.claims)+1}"
        record = {**args, 'claim_id': cid, 'state': state, 'requested_state': requested,
                  'issue': issue, 'controller_proven': False}
        self.claims[cid] = record
        detail = self.registry.evidence.write_artifact(json.dumps(record).encode(), media_type='application/json')
        self.registry.evidence.append('claim_recorded', claim_id=cid, state=state, details=detail,
                                      requested_state=requested, controller_proven=False)
        return self.reply({'status': 'recorded', **record})

    def check_candidate(self, args):
        cid = args['candidate_id']
        self.selected([cid], self.candidates)
        candidate = self.candidates[cid]
        raw, _ = self.registry.evidence.read_artifact(candidate['candidate_evidence']['artifact'])
        data = self.input_bytes(args['path'], maximum=1024*1024)
        from ctfbot.packs.checks import CHECK
        source_hash = hashlib.sha256(data).hexdigest()
        outcome = self.registry._run_command({'argv': ['python3', '-I', '-u', '-c', CHECK,
            args['operation'], args['path'], source_hash, base64.b64encode(raw).decode(), args.get('selector','')]})
        # Read private raw stdout, not a presentation preview, to determine the check's recorded result.
        stdout = outcome.result.get('stdout_evidence')
        result = {}
        if isinstance(stdout, dict):
            content, _ = self.registry.evidence.read_artifact(stdout['artifact'])
            try: result = json.loads(content)
            except (ValueError, UnicodeError): pass
        if not isinstance(result, dict): result = {}
        check_id = f"check-{len(self.checks)+1}"
        record = {'check_id': check_id, 'candidate_id': cid, 'candidate_sha256': candidate['candidate_sha256'],
                  'checker': args['operation']+':v1', 'checker_sha256': hashlib.sha256(CHECK.encode()).hexdigest(),
                  'source_path': args['path'], 'source_sha256': source_hash, 'selector': args.get('selector',''),
                  'passed': result.get('passed') is True and outcome.result.get('exit_code') == 0,
                  'validation_level': 'local_check', 'verified': False,
                  'limitation': 'Checks only the selected relation; never proof of flag correctness.',
                  'error_kind': result.get('error_kind') or outcome.result.get('error_kind'),
                  'message': result.get('message'),
                  'execution': outcome.result}
        self.checks[check_id] = record
        candidate['check_ids'].append(check_id)
        detail = self.registry.evidence.write_artifact(json.dumps(record).encode(), media_type='application/json')
        self.registry.evidence.append('candidate_check_recorded', check_id=check_id, candidate_id=cid,
                                      passed=record['passed'], validation_level='local_check', verified=False, details=detail)
        return self.reply({'status': 'local_check_passed' if record['passed'] else 'local_check_failed',
                           **record}, success=True)

    def complete(self, args):
        if self.completion is not None:
            return self.reply({"status": "already_completed", "outcome": self.completion["outcome"]})
        self.selected(args.get("observation_ids", []), self.observations)
        cid = args.get("candidate_id")
        if args["outcome"] == "candidate_unverified" and cid not in self.candidates:
            raise ValueError("candidate_unverified requires a recorded candidate_id")
        if cid is not None and cid not in self.candidates:
            raise ValueError("candidate_id is not owned by this run")
        if not args["summary"].strip():
            raise ValueError("completion summary must explain the result or remaining uncertainty")
        self.completion = {**args, "contract_version": CONTRACT_VERSION, "verified": False}
        ref = self.registry.evidence.write_artifact(json.dumps(self.completion).encode(), media_type="application/json")
        self.registry.evidence.append("completion_recorded", outcome=args["outcome"], candidate_id=cid,
                                      details=ref, unresolved_count=len(args["unresolved"]), verified=False)
        return self.reply({"status": "run_complete", "outcome": args["outcome"], "candidate_id": cid,
                           "verified": False, "details": ref, "contract_version": CONTRACT_VERSION})
