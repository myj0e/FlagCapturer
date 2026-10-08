from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ctfbot.agent.loop import AgentLoop, RunLimits
from ctfbot.application.service import LocalChallengeService
from ctfbot.benchmark.evaluation import _aggregate
from ctfbot.evidence.store import EvidenceStore
from ctfbot.model_adapters.protocol import FakeModelSession, ScriptedTurn, ToolCall
from ctfbot.reporting import generate_basic_report
from ctfbot.reporting.diagnosis import diagnose_run
from ctfbot.tools.registry import CommandResult, ToolRegistry
from test_stage_b_tui import IMAGE, QuietRuntime, make_workspace


def registry(tmp_path, content=b'authored', filename='input.txt', maximum=16384, runtime=None):
    source, work = tmp_path/'input', tmp_path/'work'
    source.mkdir(); work.mkdir()
    (source/filename).write_bytes(content)
    evidence = EvidenceStore(tmp_path/'run')
    return ToolRegistry(source, work, evidence, runtime, None, max_model_output_bytes=maximum)


def call(tools, name, **args):
    outcome = tools.invoke(ToolCall(name, args))
    assert outcome.reply.success, outcome.reply.content
    return outcome, json.loads(outcome.reply.content)


@pytest.mark.parametrize('content', [b'\x7fELFauthored', b'plain authored text', b'HTTP/1.1 200 OK\r\n\r\nunknown'])
def test_explicit_unsolved_stops_once_independently_of_input_type(tmp_path, content):
    tools = registry(tmp_path, content)
    model = FakeModelSession([
        ScriptedTurn(tool_calls=(ToolCall('challenge_list', {'path':'.'}),
            ToolCall('run_complete', {'outcome':'unsolved', 'summary':'No justified next experiment', 'unresolved':['unknown key']}),
            ToolCall('command_run', {'argv':['must-not-run']}))),
        ScriptedTurn(text='must not continue'),
    ])
    result = AgentLoop(model, tools, tools.evidence).run('authored task')
    assert result.status == 'unsolved' and not result.verified and result.turns == 1
    assert model._index == 1 and model.closed
    events = [json.loads(line) for line in tools.evidence.events_path.read_text().splitlines()]
    assert sum(e['event_type']=='completion_recorded' for e in events) == 1
    assert [e['result']['status'] for e in events if e['event_type']=='tool_result'][-1] == 'already_completed'


@pytest.mark.parametrize('content', [b'\x7fELFauthored', b'ciphertext', b'HTTP/1.1 200 OK\r\n\r\ndecoy'])
def test_format_candidate_does_not_complete_or_verify_and_can_be_checked_later(tmp_path, content):
    tools = registry(tmp_path, content)
    model = FakeModelSession([ScriptedTurn(tool_calls=(
        ToolCall('candidate_submit', {'candidate':'flag{decoy}', 'unchecked_reason':'Only a guess'}),
        ToolCall('challenge_read_bytes', {'path':'input.txt', 'offset':0, 'length':16}),
        ToolCall('run_complete', {'outcome':'candidate_unverified', 'candidate_id':'candidate-1', 'summary':'Not established', 'unresolved':['no trusted check']}),
    ))])
    result = AgentLoop(model, tools, tools.evidence).run('authored task')
    assert result.status == 'candidate_unverified' and result.candidate_ids == ('candidate-1',) and not result.verified
    assert result.tool_calls == 3
    assert not tools.reliability.candidates['candidate-1']['verified']


def test_no_completion_continuation_never_demands_a_guess_and_persists_actual_prompt(tmp_path):
    tools = registry(tmp_path)
    model = FakeModelSession([
        ScriptedTurn(tool_calls=(ToolCall('challenge_list', {'path':'.'}),), text='No result from this limited search.'),
        ScriptedTurn(tool_calls=(ToolCall('run_complete', {'outcome':'unsolved', 'summary':'No justified step', 'unresolved':[]}),)),
    ])
    result = AgentLoop(model, tools, tools.evidence).run('authored task')
    assert result.status == 'unsolved' and result.turns == 2
    events = [json.loads(line) for line in tools.evidence.events_path.read_text().splitlines()]
    prompts = [e for e in events if e['event_type']=='model_turn_started']
    text, _ = tools.evidence.read_artifact(prompts[1]['prompt_artifact']['artifact'])
    assert hashlib.sha256(text).hexdigest() == prompts[1]['prompt_sha256']
    assert b'a flag is not required' in text and b'Do not fabricate a candidate' in text
    assert any(e['event_type']=='controller_decision' and e['action']=='continue' for e in events)


def test_provider_interruption_after_accepted_completion_keeps_unsolved(tmp_path):
    class InterruptedProvider(FakeModelSession):
        def cancel(self):
            raise RuntimeError('provider cancel failed')

        def run_turn(self, prompt, tools, on_tool_call, *, timeout):
            super().run_turn(prompt, tools, on_tool_call, timeout=timeout)
            raise TimeoutError('provider interrupted after explicit completion')

    tools = registry(tmp_path)
    model = InterruptedProvider([ScriptedTurn(tool_calls=(ToolCall('run_complete', {
        'outcome':'unsolved', 'summary':'Insufficient evidence', 'unresolved':[]}),))])
    result = AgentLoop(model, tools, tools.evidence).run('authored task')
    assert result.status == 'unsolved' and not result.verified and model.closed


def test_completion_rejects_invented_candidate_and_trusted_outcome(tmp_path):
    tools = registry(tmp_path)
    for arguments in (
        {'outcome':'candidate_unverified', 'candidate_id':'candidate-invented', 'summary':'guess', 'unresolved':[]},
        {'outcome':'verified', 'summary':'self reported', 'unresolved':[]},
    ):
        outcome = tools.invoke(ToolCall('run_complete', arguments))
        assert not outcome.reply.success
    assert tools.reliability.completion is None


def test_candidate_without_completion_can_exhaust_budget(tmp_path):
    tools = registry(tmp_path)
    model = FakeModelSession([ScriptedTurn(tool_calls=(ToolCall('candidate_submit', {
        'candidate':'flag{guess}', 'unchecked_reason':'No checker yet'}),))])
    result = AgentLoop(model, tools, tools.evidence, limits=RunLimits(max_turns=1)).run('authored task')
    assert result.status == 'budget_exhausted' and not result.verified
    assert result.candidate_ids == ('candidate-1',)


@pytest.mark.parametrize('limit', [256,512,1024])
def test_clipped_text_has_bounded_traceable_reply_and_focused_raw_read(tmp_path, limit):
    tools = registry(tmp_path, b'prefix '+b'a'*2000+b' target', maximum=limit)
    outcome, reply = call(tools, 'challenge_read_text', path='input.txt')
    assert len(outcome.reply.content.encode()) <= limit
    assert outcome.result['presentation_truncated']
    raw, _ = tools.evidence.read_artifact(outcome.result['raw_response_evidence']['artifact'])
    assert b'target' in raw
    # A small read fits even when the original text summary was clipped.
    focused, _ = call(tools, 'challenge_read_bytes', path='input.txt', offset=2007, length=7)
    data, _ = tools.evidence.read_artifact(focused.result['raw_response_evidence']['artifact'])
    assert json.loads(data)['data_hex'] == b' target'.hex()
    read, _ = call(tools, 'artifact_read', artifact=outcome.result['raw_response_evidence']['artifact'], offset=0,length=20,encoding='hex')
    raw_read, _ = tools.evidence.read_artifact(read.result['raw_response_evidence']['artifact'])
    assert json.loads(raw_read)['data'] == raw[:20].hex()


def test_protocol_response_has_coverage_and_focused_access_without_new_connection(tmp_path):
    class Runtime:
        remote_spec = SimpleNamespace(endpoint='127.0.0.1:2345')
        requests = 0
        def remote_exchange(self, host, port, protocol, data, *, timeout, maximum_bytes):
            assert (host,port,protocol)==('127.0.0.1',2345,'tcp')
            self.requests += 1
            return {'data':b'HTTP/1.1 200 OK\r\n\r\n'+b'x'*maximum_bytes, 'status':'output_limit', 'sent_bytes':len(data)}
    runtime = Runtime()
    tools = registry(tmp_path, runtime=runtime)
    outcome, _ = call(tools,'remote_tcp_exchange',host='127.0.0.1',port=2345,protocol='tcp',data_base64='')
    assert outcome.result['coverage']['truncated']
    ref = outcome.result['response_evidence']
    read, _ = call(tools,'artifact_read',artifact=ref['artifact'],offset=0,length=16,encoding='utf8')
    raw, _ = tools.evidence.read_artifact(read.result['raw_response_evidence']['artifact'])
    assert json.loads(raw)['data']=='HTTP/1.1 200 OK\r'
    assert runtime.requests == 1


def test_bad_copy_and_conflicting_summary_cannot_promote_hypothesis(tmp_path):
    class Runtime:
        def execute(self, argv, *, timeout): return CommandResult(0,b'exit zero only',b'')
    tools = registry(tmp_path, b'{"cipher":"correct","decoy":"wrong"}', runtime=Runtime())
    source, _ = call(tools,'challenge_read_bytes',path='input.txt',offset=11,length=7)
    execution, _ = call(tools,'command_run',argv=['authored-hypothesis-experiment'])
    selected_ref = source.result['evidence']
    experiment, _ = call(tools,'experiment_record', execution_observation_id=execution.result['observation_id'],
        observation_ids=[source.result['observation_id']], parameters={'cipher':'wrong'},
        transformations=['copied cipher'], purpose='Check a copied value', reported_result='supports',
        bindings=[{'observation_id':source.result['observation_id'], 'artifact_path':selected_ref['artifact'],
                   'offset':0,'length':7,'sha256':hashlib.sha256(b'correct').hexdigest(),
                   'locator':'/cipher','parameter':'cipher','representation':'utf8'}])
    assert not experiment.result['source_consistent'] and not experiment.result['verified']
    assert experiment.result['source_checks'][0]['source_hash_matches']
    assert experiment.result['source_checks'][0]['parameter_matches'] is False
    claim, _ = call(tools,'claim_record',statement='Copied cipher is correct',state='supported',
        observation_ids=[source.result['observation_id']],experiment_ids=[experiment.result['experiment_id']],unknowns=['original field'])
    assert claim.result['state']=='hypothesis' and claim.result['controller_proven'] is False
    unbound, _ = call(tools,'experiment_record',execution_observation_id=execution.result['observation_id'],
        observation_ids=[],parameters={'hardcoded':'guess'},transformations=[],purpose='finite search',bindings=[],reported_result='supports')
    assert unbound.result['status']=='hypothesis_experiment'
    from ctfbot.runtime.service_recovery import write_private
    from ctfbot.reporting.replay import audit_run
    write_private(tools.evidence.run_dir/'run.json', {'run_id':'authored','challenge_id':'authored'})
    assert audit_run(tools.evidence.run_dir)['integrity']=='passed'


class AuthoredRuntime:
    """Unit-only execution of the authored checker program on authored bytes."""
    def __init__(self, root): self.root = root
    def execute(self, argv, *, timeout):
        translated = [a.replace('/challenge', str(self.root)) for a in argv]
        result = subprocess.run(translated,capture_output=True,timeout=timeout)
        return CommandResult(result.returncode,result.stdout,result.stderr)


@pytest.mark.parametrize('operation,content,selector',[
    ('base64_equals',base64.b64encode(b'flag{checked}'),''),
    ('json_field_equals',b'{"payload":{"flag":"flag{checked}"}}','/payload/flag'),
    ('http_body_equals',b'HTTP/1.1 200 OK\r\n\r\nflag{checked}',''),
])
def test_local_checks_bind_input_and_do_not_become_trusted_oracles(tmp_path,operation,content,selector):
    tools = registry(tmp_path,content)
    tools.runtime = AuthoredRuntime(tools.challenge_root)
    candidate, _ = call(tools,'candidate_submit',candidate='flag{checked}',unchecked_reason='await local check')
    check, _ = call(tools,'candidate_check',candidate_id=candidate.result['candidate_id'],operation=operation,path='input.txt',selector=selector)
    assert check.result['passed'] and check.result['validation_level']=='local_check' and not check.result['verified']
    assert check.result['source_sha256']==hashlib.sha256(content).hexdigest()
    other, _ = call(tools,'candidate_submit',candidate='flag{decoy}')
    bad, _ = call(tools,'candidate_check',candidate_id=other.result['candidate_id'],operation=operation,path='input.txt',selector=selector)
    assert not bad.result['passed'] and not bad.result['verified']
    transferred = tools.invoke(ToolCall('candidate_submit',{'candidate':'flag{other}', 'check_ids':[check.result['check_id']]}))
    assert not transferred.reply.success


def test_errors_distinguish_api_import_dependency_input_and_hash(tmp_path):
    tools = registry(tmp_path,b'not JSON')
    tools.runtime=AuthoredRuntime(tools.challenge_root)
    _, _ = call(tools,'candidate_submit',candidate='flag{x}')
    check, _ = call(tools,'candidate_check',candidate_id='candidate-1',operation='json_field_equals',path='input.txt',selector='/flag')
    assert check.result['error_kind']=='input_format_mismatch'
    api = tools.invoke(ToolCall('command_run',{'argv':['python3','-c','raise ImportError("cannot import name integer_nthroot")']}))
    missing = tools.invoke(ToolCall('command_run',{'argv':['python3','-c','import ctfbot_nonexistent_authored_dependency']}))
    assert api.result['error_kind']=='api_usage_error'
    assert missing.result['error_kind']=='dependency_missing'
    source, _ = call(tools,'challenge_read_bytes',path='input.txt',offset=0,length=8)
    ref=source.result['evidence']['artifact']
    (tools.evidence.run_dir/ref).write_bytes(b'tampered')
    bad = tools.invoke(ToolCall('artifact_read',{'artifact':ref,'offset':0,'length':8,'encoding':'hex'}))
    assert not bad.reply.success and bad.result['error_kind']=='source_mismatch'
    unknown=tools.invoke(ToolCall('artifact_read',{'artifact':'artifacts/sha256-'+('a'*64)+'.bin','offset':0,'length':8,'encoding':'hex'}))
    assert unknown.result['status']=='policy_denied'


def test_diagnosis_is_read_only_indexes_model_visible_evidence_and_redacts_raw_content(tmp_path):
    workspace,_ = make_workspace(tmp_path)
    service = LocalChallengeService(model_factory=lambda:FakeModelSession([
        ScriptedTurn(tool_calls=(ToolCall('challenge_list',{'path':'.'}),),text='PRIVATE_PUBLIC_MESSAGE_SENTINEL'),
        ScriptedTurn(tool_calls=(ToolCall('candidate_submit',{'candidate':'flag{PRIVATE_CANDIDATE_SENTINEL}'}),
            ToolCall('run_complete',{'outcome':'candidate_unverified','candidate_id':'candidate-1',
                                   'summary':'PRIVATE_SUMMARY_SENTINEL','unresolved':['unknown']}))),
    ]),model_metadata={'provider':'synthetic'},runtime_factory=QuietRuntime)
    result=service.run(workspace,None,IMAGE,tmp_path/'runs',RunLimits(max_turns=3))
    root=Path(result.run_dir)
    before={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
    with patch('subprocess.Popen',side_effect=AssertionError('diagnosis executed a command')):
        report=diagnose_run(root)
    after={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
    assert before==after and report['result_contract_version']==2
    assert len(report['controller_prompts'])==2 and all(t['controller_prompt'] for t in report['controller_prompts'])
    assert report['tool_trace'][0]['model_received'] and report['tool_trace'][0]['raw_tool_reply']
    assert report['summary']['candidate_records']==1
    serialized=json.dumps(report)
    for sentinel in ('PRIVATE_PUBLIC_MESSAGE_SENTINEL','PRIVATE_CANDIDATE_SENTINEL','PRIVATE_SUMMARY_SENTINEL'):
        assert sentinel not in serialized and sentinel not in generate_basic_report(root).read_text()
    assert report['summary']['final']['status']=='candidate_unverified'
    metrics=_aggregate([{'run_id':result.run_id,'status':result.status,'verified':False,'candidate_submissions':1}])
    assert metrics['verified_over_eligible_started']==[0,1]


def test_default_run_accepts_more_than_sixty_tool_requests(tmp_path):
    tools = registry(tmp_path)
    model = FakeModelSession([ScriptedTurn(tool_calls=tuple(
        [ToolCall('challenge_list', {'path':'.'}) for _ in range(70)] +
        [ToolCall('run_complete', {'outcome':'unsolved','summary':'No justified candidate','unresolved':[]})]))])
    result = AgentLoop(model, tools, tools.evidence).run('authored task')
    assert RunLimits().max_tool_calls is None
    assert RunLimits(max_tool_calls=1000).max_tool_calls == 1000
    assert result.status == 'unsolved' and result.tool_calls == 71
    events = [json.loads(line) for line in tools.evidence.events_path.read_text().splitlines()]
    assert not any(e.get('result',{}).get('status')=='budget_denied' for e in events)
    finished = next(e for e in events if e['event_type']=='run_finished')
    assert finished['call_counts']['observation']=={'requested':70,'admitted':70,'rejected':0}


def test_explicit_budget_still_allows_summary_without_hiding_exhaustion(tmp_path):
    tools = registry(tmp_path)
    model = FakeModelSession([ScriptedTurn(tool_calls=(
        ToolCall('challenge_list', {'path':'.'}),
        ToolCall('command_run', {'argv':['must-not-execute']}),
        ToolCall('run_complete', {'outcome':'unsolved','summary':'Stopped at explicit budget','unresolved':[]}),
    ))])
    result = AgentLoop(model, tools, tools.evidence, limits=RunLimits(max_tool_calls=1)).run('authored task')
    assert result.status == 'budget_exhausted' and result.stop_reason == 'budget_exhausted'
    assert tools.reliability.completion['summary']=='Stopped at explicit budget'
    assert tools.reliability.budget_feedback['remaining_tool_calls']==0


@pytest.mark.parametrize('content,offset,length', [(b'prefix correct suffix',7,7),(b'{"value":"correct"}',10,7)])
def test_focused_source_ref_avoids_manual_coordinates_and_reports_bad_parameter_key(tmp_path,content,offset,length):
    tools = registry(tmp_path, content, runtime=AuthoredRuntime(tmp_path/'input'))
    _, reply = call(tools,'challenge_read_bytes',path='input.txt',offset=offset,length=length)
    execution, _ = call(tools,'command_run',argv=['python3','-c','print("authored execution")'])
    arguments = dict(execution_observation_id=execution.result['observation_id'],observation_ids=[],
        parameters={'value':'correct'},transformations=[],purpose='Check literal copy',reported_result='supports',
        bindings=[{'source_ref':reply['source_ref'],'parameter_key':'correct','representation':'utf8'}])
    rejected = tools.invoke(ToolCall('experiment_record',arguments))
    assert not rejected.reply.success and 'parameter_key' in rejected.reply.content
    arguments['bindings'][0]['parameter_key']='value'
    experiment, _ = call(tools,'experiment_record',**arguments)
    assert experiment.result['source_consistent'] and not experiment.result['verified']
    arguments['bindings'][0]['offset']=offset+1
    assert not tools.invoke(ToolCall('experiment_record',arguments)).reply.success
