"""Recovery contracts tested with authored data and a deliberately forgetful provider."""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import pytest

from ctfbot.agent.context import encode
from ctfbot.agent.loop import AgentLoop, RunLimits
from ctfbot.model_adapters.codex_app_server import CodexAppServer
from ctfbot.model_adapters.events import normalize_event
from ctfbot.model_adapters.mailbox import MessageBuffer, MessageQueue
from ctfbot.model_adapters.protocol import FakeModelSession, ToolCall, TurnResult
from ctfbot.runtime.sessions import InteractiveReadResult
from ctfbot.tools.registry import CommandResult
from test_agent_reliability import call, registry

SUMMARY = dict(goal='Find an authored result', facts=[], hypotheses=['wrong key'],
               approach='Try a different key', next_steps=['inspect checkpoint'],
               blockers=['first key failed'], corrections=['first key was wrong'])


class Runtime:
    def __init__(self):
        self.executions = 0
        self.reads = 0
        self.closed_callbacks = {}

    def execute(self, argv, *, timeout):
        self.executions += 1
        return CommandResult(1 if argv == ['false'] else 0, b'', b'wrong key' if argv == ['false'] else b'')

    def start_interactive(self, session_id, argv, *, on_output, on_closed, **kwargs):
        self.closed_callbacks[session_id] = on_closed
        on_output(b'ready\n')

    start_script_session = start_interactive

    def send_interactive(self, *args):
        pass

    def read_interactive(self, session_id, *, timeout, maximum_bytes):
        self.reads += 1
        return InteractiveReadResult(b'ready\n', 'running', None)

    def close_interactive(self, session_id):
        self.closed_callbacks[session_id]('closed', 0)
        return InteractiveReadResult(b'', 'closed', 0)


def test_forgetful_provider_recovers_summary_failure_script_and_session(tmp_path):
    runtime = Runtime()
    tools = registry(tmp_path, runtime=runtime)
    class Forgetful(FakeModelSession):
        def run_turn(self, prompt, specs, dispatch, *, timeout):
            if self._index == 0:
                def send(name, **args):
                    return json.loads(dispatch(ToolCall(name, args)).content)
                send('script_save', path='solve.py', source="print('ready',flush=True)")
                send('script_start', path='solve.py', argv=[])
                failed = send('command_run', argv=['false'])
                send('experiment_record', execution_observation_id=failed['observation']['id'],
                     observation_ids=[], parameters={'key':'first'}, transformations=[], purpose='test key',
                     bindings=[], reported_result='contradicts')
                send('summary_update', **SUMMARY)
                self._index += 1
                return TurnResult('First key failed.', tool_calls=5)
            # No old response retained: recover everything through current tools.
            assert len(prompt.encode()) < 10000 and 'Try a different key' in prompt
            def read(section):
                return json.loads(dispatch(ToolCall('state_read', {'section':section})).content)['records']
            assert read('summary')[-1]['next_steps'] == ['inspect checkpoint']
            assert read('experiments')[0]['reported_result'] == 'contradicts'
            script = read('scripts')[0]
            assert script['path'] == 'solve.py' and script['last_execution']['session_id']
            session = read('sessions')[0]
            assert session['id'] == script['last_execution']['session_id']
            assert session['freshness'] == 'unknown' and runtime.reads == 0
            dispatch(ToolCall('session_close', {'session_id':session['id']}))
            dispatch(ToolCall('run_complete', {'outcome':'unsolved','summary':'Recovered state','unresolved':[]}))
            return TurnResult('', tool_calls=6)
    result = AgentLoop(Forgetful([]), tools, tools.evidence).run('authored task')
    assert result.status == 'unsolved' and result.turns == 2
    assert runtime.executions == 2  # save plus failed experiment; no repeated execution
    assert tools.context_state.records('sessions')[session_id := next(iter(tools.session_records))]['freshness'] == 'historical'
    assert session_id


def test_thousand_records_page_with_revision_bound_cursors_and_missing_ids(tmp_path):
    tools = registry(tmp_path)
    tools.reliability.context['run_id'] = 'owned-run'
    tools.reliability.experiments.update({f'experiment-{i}': {'reported_result':'contradicts', 'purpose':'错'*5000}
                                        for i in range(1000)})
    tools.context_state.changed()
    ids, cursor = [], None
    while True:
        args = {'section':'experiments', **({'cursor':cursor} if cursor else {})}
        outcome, payload = call(tools, 'state_read', **args)
        assert len(outcome.reply.content.encode()) <= 8192 and len(payload['records']) <= 20
        ids.extend(r['id'] for r in payload['records'])
        cursor = payload['next_cursor']
        if cursor is None:
            break
    assert len(ids) == len(set(ids)) == 1000
    first = json.loads(tools.invoke(ToolCall('state_read', {'section':'experiments', 'limit':1})).reply.content)
    cursor = first['next_cursor']
    invalid = [cursor[:-2], base64.urlsafe_b64encode(encode(['other','experiments',tools.context_state.revision,1]).encode()).decode(),
               base64.urlsafe_b64encode(encode(['owned-run','claims',tools.context_state.revision,1]).encode()).decode(),
               base64.urlsafe_b64encode(encode(['owned-run','experiments',tools.context_state.revision,1000]).encode()).decode()]
    for token in invalid:
        assert not tools.invoke(ToolCall('state_read', {'section':'experiments','cursor':token})).reply.success
    tools.context_state.changed()
    assert 'stale' in tools.invoke(ToolCall('state_read', {'section':'experiments','cursor':cursor})).reply.content
    assert not tools.invoke(ToolCall('state_read', {'section':'candidates','id':'candidate-other'})).reply.success
    snapshot = tools.context_state.snapshot(reason='test', constraints={})
    assert len(snapshot.encode()) <= 8192 and 'experiments' in json.loads(snapshot)['omitted_sections']


def test_summary_history_and_retrieval_do_not_allow_summary_spin(tmp_path):
    tools = registry(tmp_path)
    call(tools, 'summary_update', **SUMMARY)
    revision = tools.context_state.revision
    call(tools, 'state_read', section='summary', id='latest')
    assert tools.context_state.revision == revision
    artifact = tools.evidence.write_artifact(b'authored')
    call(tools, 'artifact_read', artifact=artifact['artifact'],offset=0,length=1,encoding='hex')
    _, reply = call(tools, 'summary_update', **SUMMARY)
    assert reply['status'] == 'summary_already_updated'
    call(tools, 'challenge_list', path='.')
    call(tools, 'summary_update', **{**SUMMARY, 'corrections':['second revision']})
    assert call(tools,'state_read',section='summary',id='1')[1]['records'][0]['corrections'] == SUMMARY['corrections']
    tools.reliability.summary = None
    assert call(tools,'state_read',section='summary',id='latest')[1]['records'][0]['revision'] == 2


def test_missing_source_is_marked_and_sessions_never_consume_output(tmp_path):
    runtime = Runtime()
    tools = registry(tmp_path, runtime=runtime)
    call(tools,'script_save',path='solve.py',source='print(1)')
    source = tools.scripts['solve.py']['source']
    (tools.evidence.run_dir/source['artifact']).unlink()
    _, payload = call(tools,'state_read',section='scripts')
    assert payload['records'][0]['source_status'][source['artifact']] == 'missing'
    _, start = call(tools,'session_start',argv=['python3'])
    sid = start['session_id']
    call(tools,'state_read',section='sessions')
    assert runtime.reads == 0
    call(tools,'session_read',session_id=sid,wait_seconds=0)
    _, payload = call(tools,'state_read',section='sessions',id=sid)
    assert payload['records'][0]['freshness'] == 'live' and runtime.reads == 1
    runtime.closed_callbacks[sid]('exited', 0)
    _, payload = call(tools,'state_read',section='sessions',id=sid)
    assert payload['records'][0]['freshness'] == 'historical' and payload['records'][0]['status'] == 'exited'


@pytest.mark.parametrize('maximum', [256,1024,4096])
def test_long_stdout_stderr_keep_tails_and_bounded_json(tmp_path, maximum):
    class Long(Runtime):
        def execute(self, argv, *, timeout):
            return CommandResult(1, b'HEAD'+b'x'*30000+b'FINAL', b'ERRHEAD'+b'y'*30000+b'ERRFINAL')
    tools = registry(tmp_path,runtime=Long(),maximum=maximum)
    outcome = tools.invoke(ToolCall('command_run',{'argv':['test']}))
    payload = json.loads(outcome.reply.content)
    assert len(outcome.reply.content.encode()) <= maximum and payload['exit_code'] == 1
    if maximum >= 1024:
        assert payload['previews']['stdout']['tail'].endswith('FINAL')
        assert payload['previews']['stderr']['tail'].endswith('ERRFINAL')
    assert b'FINAL' in tools.evidence.read_artifact(outcome.result['stdout_evidence']['artifact'])[0]


def test_artifact_tail_literal_find_binary_unicode_and_integrity(tmp_path):
    tools = registry(tmp_path)
    data = b'\xff'+('汉'*20).encode()+b'needle'*30
    ref = tools.evidence.write_artifact(data)
    _, tail = call(tools,'artifact_tail',artifact=ref['artifact'],length=13,encoding='hex')
    assert bytes.fromhex(tail['data']) == data[-13:]
    _, found = call(tools,'artifact_find',artifact=ref['artifact'],literal='needle',offset=0,scan_bytes=1048576)
    assert found['matches_in_scan'] == 30 and found['matches_omitted'] == 10 and len(found['offsets']) == 20
    _, partial = call(tools,'artifact_find',artifact=ref['artifact'],literal='needle',offset=1,scan_bytes=10)
    assert partial['coverage']['partial_scan'] and partial['matches_in_scan'] == 0
    assert not tools.invoke(ToolCall('artifact_tail',{'artifact':'../secret','length':10,'encoding':'utf8'})).reply.success
    (tools.evidence.run_dir/ref['artifact']).write_bytes(b'tampered')
    assert not tools.invoke(ToolCall('artifact_tail',{'artifact':ref['artifact'],'length':10,'encoding':'utf8'})).reply.success


def test_normal_budget_exhaustion_preserves_recovery_completion_and_counts_denials(tmp_path):
    runtime = Runtime()
    tools = registry(tmp_path,runtime=runtime)
    observed = []
    class Provider(FakeModelSession):
        def run_turn(self, prompt, specs, dispatch, *, timeout):
            for tool in [ToolCall('command_run',{'argv':['first']}), ToolCall('command_run',{'argv':['denied']}),
                         ToolCall('state_read',{'section':'overview'}),
                         ToolCall('run_complete',{'outcome':'unsolved','summary':'Budget used','unresolved':[]})]:
                reply = dispatch(tool)
                observed.append(reply.content)
            return TurnResult('',tool_calls=4)
    loop = AgentLoop(Provider([]),tools,tools.evidence,limits=RunLimits(total_model_output_bytes=256))
    result = loop.run('authored')
    assert result.status == 'budget_exhausted' and runtime.executions == 1
    assert json.loads(observed[2])['section'] == 'overview' and tools.reliability.completion
    telemetry = loop.reply_budget.telemetry()
    assert telemetry['tool_reply_bytes'] == sum(len(s.encode()) for s in observed)
    assert telemetry['pools']['recovery']['used'] > 0 and telemetry['pools']['closing']['used'] > 0
    assert all(p['used'] <= p['limit'] for p in telemetry['pools'].values())


def event(kind, epoch='compact-1'):
    return {'type':kind,'epoch':epoch,'thread_id':'thread','turn_id':'turn','sequence':1,
            'timestamp_utc':datetime.now(timezone.utc).isoformat(),'source':'synthetic'}


@pytest.mark.parametrize('small_budget', [False,True])
def test_compaction_dedup_and_actual_tool_reply_restoration(tmp_path, small_budget):
    tools = registry(tmp_path)
    replies = []
    class Provider(FakeModelSession):
        def set_event_handler(self, handler):
            self.handler = handler
        def run_turn(self, prompt, specs, dispatch, *, timeout):
            self.handler(event('compaction_completed'))
            self.handler(event('compaction_completed'))
            replies.append(dispatch(ToolCall('state_read',{'section':'overview'})).content)
            replies.append(dispatch(ToolCall('state_read',{'section':'summary'})).content)
            dispatch(ToolCall('run_complete',{'outcome':'unsolved','summary':'done','unresolved':[]}))
            return TurnResult('',tool_calls=3)
    limits = RunLimits(recovery_output_bytes=256) if small_budget else RunLimits()
    loop = AgentLoop(Provider([]),tools,tools.evidence,limits=limits)
    loop.run('authored')
    events = [json.loads(line) for line in tools.evidence.events_path.read_text().splitlines()]
    restores = [e for e in events if e['event_type']=='context_restore_delivered']
    assert len([e for e in events if e['event_type']=='provider_compaction_completed']) == 1
    if small_budget:
        assert not restores and loop.context_events.pending == 'compact-1'
    else:
        assert len(restores) == 1 and json.loads(replies[0])['context_restore']['run_id'] == loop.run_id
        assert 'context_restore' not in json.loads(replies[1])
    assert all(len(s.encode()) <= limits.per_tool_output_bytes for s in replies)


def test_provider_schema_normalization_does_not_guess_occupancy_or_accept_wrong_turn():
    message = {'method':'thread/tokenUsage/updated','params':{'threadId':'thread','turnId':'turn',
               'tokenUsage':{'last':{'inputTokens':10},'total':{'inputTokens':100},'modelContextWindow':200}}}
    normalized = normalize_event(message,'thread','turn',1)
    assert normalized['current_context_tokens'] is None and normalized['capacity'] == 200
    assert normalize_event(message,'other','turn',1) is None
    assert normalize_event(message,'thread','other',1) is None


def test_usage_cumulative_snapshots_are_deltas_and_queue_is_bounded(tmp_path):
    tools = registry(tmp_path)
    loop = AgentLoop(FakeModelSession([]), tools, tools.evidence)
    context = loop.context_events
    for value in (100,100,150,120,150):
        context.enqueue({**event('context_usage'), 'cumulative_usage':{'inputTokens':value}, 'capacity':1000})
        context.drain()
    assert context.billing_totals == {'inputTokens':150}
    for _ in range(1000):
        context.enqueue(event('context_usage'))
    assert len(context._queue) == 1


def test_mailboxes_coalesce_usage_preserve_critical_and_fail_explicitly():
    buffer = MessageBuffer(maximum=4)
    for i in range(1000):
        buffer.append({'method':'thread/tokenUsage/updated','params':{'threadId':'t','turnId':'u','i':i}})
    assert len(buffer) == 1 and buffer[0]['params']['i'] == 999
    request = {'method':'item/tool/call','id':10}
    terminal = {'method':'turn/completed','params':{'turn':{'id':'u'}}}
    buffer.append(request); buffer.append(terminal)
    for i in range(100):
        buffer.append({'method':'unrelated','params':{'i':i}})
    assert request in buffer and terminal in buffer and len(buffer) <= 4
    mailbox = MessageQueue(maximum=1)
    mailbox.put(request); mailbox.put(terminal)
    with pytest.raises(OverflowError):
        mailbox.get(timeout=.01)


def test_server_consumes_stale_events_and_answers_unknown_request_once():
    server = CodexAppServer(executable='synthetic')
    sent = []
    server._send = sent.append
    server._messages.put({'method':'unknown/request','id':99,'params':{}})
    server._messages.put({'method':'thread/compacted','params':{'threadId':'old','turnId':'old'}})
    server._messages.put({'method':'thread/compacted','params':{'threadId':'t','turnId':'u'}})
    actual = server._take_dynamic_turn_message('t','u',.1)
    assert actual['params']['threadId'] == 't' and len(sent) == 1 and sent[0]['id'] == 99
    assert not server._notifications and not server._unmatched


def test_compaction_restores_at_next_continuation_and_charges_prompt_separately(tmp_path):
    tools = registry(tmp_path)
    class Provider(FakeModelSession):
        def set_event_handler(self, handler):
            self.handler = handler
        def run_turn(self, prompt, specs, dispatch, *, timeout):
            if self._index == 0:
                dispatch(ToolCall('challenge_list', {'path':'.'}))
                self.handler(event('compaction_completed'))
                self._index += 1
                return TurnResult('',tool_calls=1)
            assert 'turn_boundary' in prompt and 'task_artifact' in prompt
            dispatch(ToolCall('run_complete', {'outcome':'unsolved','summary':'done','unresolved':[]}))
            return TurnResult('',tool_calls=1)
    loop = AgentLoop(Provider([]),tools,tools.evidence)
    loop.run('authored task')
    events = [json.loads(line) for line in tools.evidence.events_path.read_text().splitlines()]
    restores = [e for e in events if e['event_type']=='context_restore_delivered']
    assert len(restores) == 1 and restores[0]['channel'] == 'continuation_prompt'
    assert loop.reply_budget.prompt_restore_bytes > 0
    replies = [e for e in events if e['event_type']=='tool_result']
    assert loop.reply_budget.tool_reply_bytes == sum(e['response_evidence']['bytes'] for e in replies)
    assert loop.reply_budget.prompt_bytes > loop.reply_budget.prompt_restore_bytes


def test_no_following_tool_or_turn_never_claims_pending_restore_delivered(tmp_path):
    tools = registry(tmp_path)
    class Provider(FakeModelSession):
        def set_event_handler(self, handler):
            self.handler = handler
        def run_turn(self, prompt, specs, dispatch, *, timeout):
            self.handler(event('compaction_completed'))
            return TurnResult('No tools.',tool_calls=0)
    loop = AgentLoop(Provider([]),tools,tools.evidence)
    loop.run('authored')
    assert loop.context_events.pending == 'compact-1'
    assert 'context_restore_delivered' not in tools.evidence.events_path.read_text()


@pytest.mark.parametrize('legacy_first', [False,True])
def test_modern_and_legacy_compaction_notifications_share_one_restore(tmp_path, legacy_first):
    tools = registry(tmp_path)
    loop = AgentLoop(FakeModelSession([]),tools,tools.evidence)
    modern = event('compaction_completed')
    legacy = {**modern, 'epoch':None, 'legacy':True}
    for item in ([legacy,modern] if legacy_first else [modern,legacy]):
        loop.context_events.enqueue(item)
        loop.context_events.drain()
    assert loop.context_events.pending == 'compact-1'
    events = [json.loads(line) for line in tools.evidence.events_path.read_text().splitlines()]
    assert sum(e['event_type']=='provider_compaction_completed' for e in events) == 1


def test_unicode_summary_snapshots_are_byte_bounded_and_missing_summary_is_explicit(tmp_path):
    tools = registry(tmp_path)
    call(tools,'summary_update',**{**SUMMARY, 'goal':'汉'*240, 'approach':'汉'*600,
         'facts':[{'text':'汉'*320,'observation_ids':[]} for _ in range(5)],
         'hypotheses':['汉'*320]*3, 'next_steps':['汉'*240]*2,
         'blockers':['汉'*240]*3, 'corrections':['汉'*320]*3})
    snapshot = tools.context_state.snapshot(reason='test',constraints={})
    assert len(snapshot.encode()) <= 8192
    outcome, _ = call(tools,'state_read',section='summary',id='latest')
    assert len(outcome.reply.content.encode()) <= 8192
    tools.reliability.summaries.clear()
    assert not tools.invoke(ToolCall('state_read',{'section':'summary','id':'latest'})).reply.success


def test_recovery_and_closing_pools_never_grow_under_denial_polling(tmp_path):
    tools = registry(tmp_path)
    replies = []
    class Provider(FakeModelSession):
        def run_turn(self, prompt, specs, dispatch, *, timeout):
            for _ in range(100):
                replies.append(dispatch(ToolCall('state_read',{'section':'overview'})).content)
            dispatch(ToolCall('run_complete',{'outcome':'unsolved','summary':'done','unresolved':[]}))
            return TurnResult('',tool_calls=101)
    loop = AgentLoop(Provider([]),tools,tools.evidence,limits=RunLimits(recovery_output_bytes=256,closing_output_bytes=256))
    loop.run('authored')
    assert loop.reply_budget.used['ordinary'] == 0
    assert loop.reply_budget.used['recovery'] <= 256 and loop.reply_budget.used['closing'] <= 256
    assert sum(len(s.encode()) for s in replies) <= 512
    assert not tools.reliability.completion


def test_multibyte_preview_and_large_binary_raw_artifact_remain_readable(tmp_path):
    class Binary(Runtime):
        def execute(self, argv, *, timeout):
            return CommandResult(0,b'\x00'*800000 + '末尾'.encode(),b'')
    tools = registry(tmp_path,runtime=Binary(),maximum=2048)
    outcome = tools.invoke(ToolCall('command_run',{'argv':['binary']}))
    payload = json.loads(outcome.reply.content)
    assert payload['previews']['stdout']['tail'].endswith('末尾')
    assert '�' not in payload['previews']['stdout']['tail']
    ref = outcome.result['raw_response_evidence']
    assert ref['bytes'] > 4 * 1024 * 1024
    _, tail = call(tools,'artifact_tail',artifact=ref['artifact'],length=20,encoding='hex')
    assert len(bytes.fromhex(tail['data'])) == 20


def test_new_turn_with_only_recovery_reads_cannot_spin_summary_updates(tmp_path):
    tools = registry(tmp_path)
    tools.reliability.call_context = {'turn':1,'public_message_revision':0}
    call(tools,'summary_update',**SUMMARY)
    tools.reliability.call_context = {'turn':2,'public_message_revision':0}
    call(tools,'state_read',section='summary')
    _, reply = call(tools,'summary_update',**SUMMARY)
    assert reply['status'] == 'summary_already_updated'
    tools.reliability.call_context['public_message_revision'] = 1
    assert call(tools,'summary_update',**SUMMARY)[1]['status'] == 'summary_updated'


def test_invalid_script_arguments_remain_errors_without_corrupting_execution_index(tmp_path):
    tools = registry(tmp_path,runtime=Runtime())
    call(tools,'script_save',path='solve.py',source='print(1)')
    assert not tools.invoke(ToolCall('script_run',{'path':42,'argv':[]})).reply.success
    assert not tools.invoke(ToolCall('script_run',{'path':'solve.py','argv':'invalid'})).reply.success
    assert tools.scripts['solve.py']['last_execution'] is None


def test_public_response_assembly_remains_bounded():
    from ctfbot.model_adapters.mailbox import PublicTextBuffer
    buffer = PublicTextBuffer(maximum=100)
    for _ in range(10000):
        buffer.append('public delta')
    buffer.append('FINAL')
    assert len(buffer.text) == 100 and buffer.result().endswith('FINAL') and buffer.truncated


def test_long_mixed_codex_stream_keeps_rpc_terminal_and_context_events():
    events, calls, sent = [], [], []
    class Server(CodexAppServer):
        def request(self, method, params=None, *, timeout=30):
            return {'turn':{'id':'u'}}
        def _send(self, message):
            sent.append(message)
    server = Server(executable='synthetic')
    for i in range(1000):
        server._messages.put({'method':'thread/tokenUsage/updated','params':{'threadId':'t','turnId':'u',
                             'tokenUsage':{'total':{'inputTokens':i},'last':{'inputTokens':1},'modelContextWindow':10000}}})
    server._messages.put({'method':'thread/compacted','params':{'threadId':'wrong','turnId':'u'}})
    for method in ('item/started','item/completed'):
        server._messages.put({'method':method,'params':{'threadId':'t','turnId':'u',
                             'item':{'type':'contextCompaction','id':'epoch-1'}}})
    server._messages.put({'method':'unknown/request','id':88,'params':{}})
    for index in (10,11):
        server._messages.put({'method':'item/tool/call','id':index,'params':{'threadId':'t','turnId':'u',
                             'tool':'state_read','arguments':{'section':'summary'}}})
    server._messages.put({'method':'turn/completed','params':{'threadId':'t','turn':{'id':'u','status':'completed'}}})
    def handle(call):
        calls.append(call)
        return {'content':'{}','success':True}
    result = server.run_dynamic_turn('t','synthetic','prompt',[],handle,timeout=1,on_event=events.append)
    assert result['tool_calls'] == 2 and [c['call_id'] for c in calls] == ['10','11']
    assert [e['type'] for e in events] == ['context_usage','compaction_started','compaction_completed']
    assert [m['id'] for m in sent if 'result' in m] == [10,11]
    assert [m['id'] for m in sent if 'error' in m] == [88]
    assert not server._notifications and not server._unmatched


def test_nested_json_crop_keeps_ids_and_references_without_partial_serializations(tmp_path):
    from ctfbot.tools.presentation import render
    tools = registry(tmp_path)
    ref = tools.evidence.write_artifact(b'authored')
    rendered = render({'status':'ok','candidate_id':'candidate-1','records':[{'nested':'x'*10000}]},256,artifact=ref)
    value = json.loads(rendered)
    assert value['candidate_id'] == 'candidate-1' and value['raw_response_artifact'] == ref['artifact']
    assert 'records' in value['omitted_fields'] and 'previews' not in value
    assert len(rendered.encode()) <= 256


@pytest.mark.parametrize('limit_kind', ['wall','count'])
def test_recovery_pool_cannot_bypass_wall_time_or_explicit_tool_count(tmp_path, limit_kind):
    from unittest.mock import patch
    tools = registry(tmp_path)
    denied = []
    class Provider(FakeModelSession):
        def run_turn(self, prompt, specs, dispatch, *, timeout):
            if limit_kind == 'count':
                dispatch(ToolCall('challenge_list', {'path':'.'}))
                reply = dispatch(ToolCall('state_read', {'section':'overview'}))
            else:
                with patch('ctfbot.agent.loop.time.monotonic', return_value=float('inf')):
                    reply = dispatch(ToolCall('state_read', {'section':'overview'}))
            denied.append(reply)
            dispatch(ToolCall('run_complete', {'outcome':'unsolved','summary':'Resource budget exhausted','unresolved':[]}))
            return TurnResult('',tool_calls=3)
    limits = RunLimits(max_tool_calls=1) if limit_kind == 'count' else RunLimits()
    result = AgentLoop(Provider([]),tools,tools.evidence,limits=limits).run('authored')
    assert result.status == 'budget_exhausted' and not denied[0].success
    assert json.loads(denied[0].content)['budget'] in {'max_tool_calls','wall_time_seconds'}
    assert tools.reliability.completion
