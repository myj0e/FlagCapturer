"""Read-only, evidence-grounded diagnosis. Never executes tools or calls models."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path

from ctfbot.reporting.replay import _artifact, _events, audit_run
from ctfbot.runtime.service_recovery import read_private


def diagnose_run(run_dir: Path) -> dict:
    audit = audit_run(run_dir)
    root = run_dir.resolve(strict=True)
    metadata = read_private(root / 'run.json')
    events = _events(root)
    turns, calls, observations, candidates, checks, decisions = [], [], [], [], [], []
    messages = []
    repeats: Counter = Counter()
    errors: Counter = Counter()
    incomplete_prompts = []
    source_mismatches, unbound_experiments, downgraded_claims = [], [], []
    claims, experiments = [], []
    last_completed, tool_contract = None, None
    for e in events:
        kind = e.get('event_type')
        seq = e['seq']
        if kind == 'run_started':
            tool_contract = e.get('tool_schema_artifact')
        elif kind == 'model_turn_started':
            ref = e.get('prompt_artifact')
            if ref is None:
                incomplete_prompts.append(e.get('turn'))
            elif ref.get('sha256') != e.get('prompt_sha256'):
                raise ValueError('controller prompt artifact differs from recorded prompt hash')
            turns.append({'seq': seq, 'turn': e.get('turn'), 'controller_prompt': ref,
                          'prompt_sha256': e.get('prompt_sha256')})
        elif kind == 'assistant_message':
            messages.append({'seq': seq, 'turn': e.get('turn'), 'public_message': e.get('evidence')})
        elif kind == 'tool_call':
            repeats[(e.get('name'), e.get('arguments_sha256'))] += 1
        elif kind == 'tool_result':
            result = e.get('result', {})
            if result.get('error_kind'): errors[result['error_kind']] += 1
            if result.get('status') in {'tool_error','command_failed','environment_error','timeout','policy_denied','invalid_arguments'} and not result.get('error_kind'):
                errors[result.get('status')] += 1
            calls.append({'seq': seq, 'turn': e.get('turn'), 'tool_index': e.get('tool_index'),
                          'name': e.get('name'), 'arguments': e.get('arguments_artifact'),
                          'call_category': result.get('call_category'), 'admitted': result.get('admitted'),
                          'execution_state': result.get('execution_state'),
                          'status': result.get('status'), 'error_kind': result.get('error_kind'),
                          'elapsed_seconds': e.get('elapsed_seconds'),
                          'model_received': e.get('response_evidence'),
                          'raw_tool_reply': result.get('raw_response_evidence'),
                          'before_loop_clipping': result.get('original_response_evidence'),
                          'presentation_truncated': bool(result.get('presentation_truncated')),
                          'runtime_truncated': bool(result.get('truncated')),
                          'observation_id': result.get('observation_id')})
        elif kind == 'observation_recorded':
            observations.append({key: e.get(key) for key in ('seq','observation_id','turn','tool_index','tool','raw_response','sources','coverage','context','historical')})
        elif kind == 'candidate_recorded':
            # Keep candidate bytes, derivation and arbitrary user text in private
            # artifacts; the diagnostic index never copies them into its output.
            candidates.append({key: e.get(key) for key in ('seq','candidate_id','status','verified','details')})
        elif kind == 'candidate_check_recorded':
            checks.append({key: e.get(key) for key in ('seq','check_id','candidate_id','passed','validation_level','verified','details')})
        elif kind in {'controller_decision','completion_recorded','run_finished','run_cancelled','sandbox_error'}:
            safe_keys = ('seq','event_type','turn','action','reason','outcome','candidate_id','details',
                         'unresolved_count','status','stop_reason','result_status','result_contract_version','call_counts')
            decisions.append({key: e[key] for key in safe_keys if key in e})
            if kind == 'run_finished': last_completed = decisions[-1]
        elif kind == 'experiment_recorded':
            experiments.append({key:e.get(key) for key in ('seq','experiment_id','source_consistent','reported_result','details')})
            record = json.loads(_artifact(root, e['details']))
            if not record.get('source_consistent'):
                unbound_experiments.append(e.get('experiment_id'))
            if any(c.get('passed') is False for c in record.get('source_checks', [])):
                source_mismatches.append(e.get('experiment_id'))
        elif kind == 'claim_recorded':
            claims.append({key:e.get(key) for key in ('seq','claim_id','state','requested_state','controller_proven','details')})
            if e.get('requested_state') != e.get('state'):
                downgraded_claims.append(e.get('claim_id'))
    repeat_rows = [{'name': name, 'arguments_sha256': digest, 'count': count,
                    'interpretation': 'Repeated identical arguments; may be a justified retry/poll, not proof of wasted work.'}
                   for (name,digest),count in repeats.items() if count > 1]
    return {'schema_version': 1, 'result_contract_version': metadata.get('result_contract_version', 1),
            'run_id': metadata.get('run_id'), 'integrity': audit,
            'summary': {'turns': len(turns), 'tool_results': len(calls), 'errors': dict(errors),
                        'actual_command_executions': sum(e.get('event_type') == 'command_execution' for e in events),
                        'candidate_records': len(candidates), 'local_checks': len(checks),
                        'local_checks_passed': sum(c.get('passed') is True for c in checks),
                        'tool_replies_clipped': sum(c['presentation_truncated'] for c in calls),
                        'source_mismatch_experiments': source_mismatches,
                        'unbound_experiments': unbound_experiments, 'downgraded_claims': downgraded_claims,
                        'final': last_completed},
            'controller_prompts': turns, 'public_messages': messages, 'tool_trace': calls,
            'tool_contract': tool_contract,
            'observations': observations, 'candidates': candidates, 'checks': checks,
            'claims': claims, 'experiments': experiments,
            'controller_decisions': decisions, 'repeated_calls': repeat_rows,
            'review_items': {'legacy_turns_without_prompt_artifact': incomplete_prompts,
                             'legacy_candidate_provenance': not candidates and any(e.get('name') == 'candidate_submit' for e in events)},
            'limits': ['Only recorded public messages are available; no hidden reasoning is collected.',
                       'Local checks and provenance hashes do not establish a trusted correct answer.',
                       'Errors and repeated calls require contextual review; no automatic reasoning-failure attribution.',
                       'Artifact references point to private evidence; no raw candidate/message/prompt/credential text is copied.',
                       'No challenge commands, Docker operations or model calls were executed.']}
