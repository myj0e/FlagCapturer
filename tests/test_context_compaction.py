"""Final acceptance: deterministic context replacement without any LLM API."""
from __future__ import annotations

import json
import threading
from unittest.mock import patch

import pytest

from ctfbot.agent.context import MAX_CONTEXT_BYTES
from ctfbot.agent.context_policy import ContextPolicy, ContextPressure
from ctfbot.agent.loop import AgentLoop
from ctfbot.application.control import RunControl
from ctfbot.model_adapters.codex_session import CodexModelSession
from ctfbot.model_adapters.protocol import ContextCompactionRequired, FakeModelSession, ToolCall, TurnResult
from ctfbot.tui.context_status import ContextStatus
from test_agent_reliability import registry
from test_context_management import Runtime, SUMMARY, event


def usage(used, *, thread='old', capacity=200000, billed=500000):
    return {**event('context_usage'), 'thread_id': thread, 'capacity': capacity,
            'current_context_tokens': used, 'cumulative_usage': {'totalTokens': billed}}


def records(tools):
    return [json.loads(line) for line in tools.evidence.events_path.read_text().splitlines()]


def finish(dispatch):
    dispatch(ToolCall('run_complete', {"writeup": '# Authored solution\n\nReproduce the recorded fixture steps; outputs and submitted results are preserved in this run.', 'outcome': 'unsolved', 'summary': 'done', 'unresolved': ['unknown key']}))


def test_strict_threshold_estimates_and_billing_are_separate():
    pressure = ContextPressure()
    pressure.observe(usage(160000))
    assert not pressure.required
    pressure.observe(usage(160001))
    assert pressure.required and pressure.describe()['basis'] == 'reported'
    pressure.reset('short seed')
    pressure.observe({**usage(None, thread='fresh', billed=99999999), 'last_inference_usage': {'inputTokens': 20, 'outputTokens': 3}})
    assert not pressure.required and pressure.used == 23
    pressure.observe({**usage(None), 'last_inference_usage': {'inputTokens': 160000, 'outputTokens': 1}})
    assert pressure.required and pressure.describe()['basis'] == 'last_inference_estimate'
    unknown = ContextPressure()
    unknown.add_text('中' * 100000)
    assert not unknown.required  # Never invent a model's window size.


def test_large_seed_does_not_compact_forever_and_configuration_is_validated():
    pressure = ContextPressure(ContextPolicy(capacity=100))
    pressure.add_text('x' * 81)
    assert pressure.required
    pressure.reset('x' * 90)
    pressure.add_text('more')
    pressure.observe(usage(95, capacity=200000))
    assert not pressure.required and pressure.capacity == 100
    pressure.observe(usage(50))
    pressure.add_text('x' * 31)
    assert pressure.required
    for capacity in (0, -1, True):
        with pytest.raises(ValueError):
            ContextPolicy(capacity=capacity)
    with pytest.raises(ValueError):
        ContextPolicy(threshold=1)


@pytest.mark.parametrize('checkpoint', [True, False])
def test_mid_turn_compaction_recovers_same_run_without_reexecuting_tools(tmp_path, checkpoint):
    runtime = Runtime()
    tools = registry(tmp_path, runtime=runtime)

    class Forgetful(FakeModelSession):
        resets = 0
        def set_event_handler(self, handler):
            self.handler = handler
        def set_context_checkpoint(self, callback):
            self.checkpoint = callback
        def reset_context(self):
            self.resets += 1
        def run_turn(self, prompt, specs, dispatch, *, timeout):
            assert timeout is None
            if not self.resets:
                dispatch(ToolCall('command_run', {'argv': ['false']}))
                dispatch(ToolCall('summary_update', SUMMARY))
                self.handler(usage(160001))
                if checkpoint:
                    self.checkpoint()
                # Fallback for adapters that only yield at tool boundaries.
                dispatch(ToolCall('command_run', {'argv': ['must-not-run-before-reset']}))
                pytest.fail('context checkpoint must yield')
            assert self.resets == 1 and 'automatic_compaction' in prompt
            assert 'Try a different key' in prompt and 'original task artifact' in prompt
            summary = json.loads(dispatch(ToolCall('state_read', {'section': 'summary'})).content)
            assert summary['records'][-1]['next_steps'] == ['inspect checkpoint']
            assert runtime.executions == 1
            # Old billing survives the new provider thread.
            self.handler(usage(10000, thread='fresh', billed=123))
            finish(dispatch)
            return TurnResult('')

    model = Forgetful([])
    loop = AgentLoop(model, tools, tools.evidence)
    result = loop.run('Original authored task\n' + 'long user data\n' * 100)
    assert result.stop_reason == 'model_explicit_completion' and model.resets == 1
    assert result.usage_totals['totalTokens'] == 500123
    events = records(tools)
    compressed = [e for e in events if e['event_type'] == 'context_compaction_completed']
    assert len(compressed) == 1 and compressed[0]['before']['used'] == 160001
    assert compressed[0]['used'] < 160000
    snapshot_bytes, _ = tools.evidence.read_artifact(compressed[0]['snapshot']['artifact'])
    snapshot = json.loads(snapshot_bytes)
    assert len(snapshot_bytes) <= MAX_CONTEXT_BYTES and snapshot['run_id'] == result.run_id
    assert snapshot['summary_is_controller_proof'] is False
    task_bytes, _ = tools.evidence.read_artifact(snapshot['constraints']['task_artifact']['artifact'])
    assert task_bytes.startswith(b'Original authored task') and len(task_bytes) > 512


def test_turn_boundary_compaction_retains_turn_usage_and_can_repeat(tmp_path):
    tools = registry(tmp_path)
    class Provider(FakeModelSession):
        resets = 0
        def set_event_handler(self, handler): self.handler = handler
        def reset_context(self): self.resets += 1
        def run_turn(self, prompt, specs, dispatch, *, timeout):
            if self.resets < 2:
                self.handler(usage(170000, thread=str(self.resets), billed=100))
            else:
                finish(dispatch)
            return TurnResult('', usage={'input_tokens': 10})
    model = Provider([])
    result = AgentLoop(model, tools, tools.evidence).run('authored task')
    assert model.resets == 2 and result.status == 'unsolved'
    assert len([e for e in records(tools) if e['event_type'] == 'model_turn_completed']) == 3


def test_unsupported_adapter_continues_without_claiming_compaction(tmp_path):
    tools = registry(tmp_path)
    class Provider(FakeModelSession):
        def set_event_handler(self, handler): self.handler = handler
        def run_turn(self, prompt, specs, dispatch, *, timeout):
            self.handler(usage(190000))
            dispatch(ToolCall('state_read', {'section': 'overview'}))
            dispatch(ToolCall('state_read', {'section': 'overview'}))
            finish(dispatch)
            return TurnResult('')
    result = AgentLoop(Provider([]), tools, tools.evidence).run('authored')
    assert result.status == 'unsolved'
    events = records(tools)
    assert len([e for e in events if e['event_type'] == 'context_compaction_unavailable']) == 1
    assert not any(e['event_type'] == 'context_compaction_completed' for e in events)


def test_cancelled_run_never_resets_context(tmp_path):
    tools = registry(tmp_path)
    control = RunControl()
    class Provider(FakeModelSession):
        def set_event_handler(self, handler): self.handler = handler
        def reset_context(self): pytest.fail('user cancellation takes priority')
        def run_turn(self, prompt, specs, dispatch, *, timeout):
            self.handler(usage(190000))
            control.cancel()
            return TurnResult('')
    result = AgentLoop(Provider([]), tools, tools.evidence, control=control).run('authored')
    assert result.status == 'user_cancelled'


def test_snapshot_archives_oversized_metadata_without_losing_original(tmp_path):
    tools = registry(tmp_path)
    value = '中' * 10000
    tools.provider_context = {'source': value}
    snapshot = tools.context_state.snapshot(reason='automatic_compaction', constraints={'large': value})
    assert len(snapshot.encode()) <= MAX_CONTEXT_BYTES
    for field in ('constraints', 'provider_context'):
        reference = json.loads(snapshot)[field]['record_artifact']['artifact']
        raw, _ = tools.evidence.read_artifact(reference)
        assert value in json.loads(raw).values()


def test_codex_reset_restarts_transport_without_compaction_api():
    calls = []
    class Server:
        def interrupt(self): calls.append('interrupt')
        def close(self): calls.append('close')
        def start(self): calls.append('start')
    model = CodexModelSession.__new__(CodexModelSession)
    model._closed = False
    model._cancelled = threading.Event()
    model._server = Server()
    model._thread_id = 'old'
    model._tool_schema_hash = 'old-schema'
    model._executable = 'authored-executable'
    with patch('ctfbot.model_adapters.codex_session.CodexAppServer', return_value=Server()) as factory:
        model.reset_context()
    assert calls == ['interrupt', 'close', 'start']
    factory.assert_called_once_with('authored-executable', experimental_api=True)
    assert model._thread_id is None and model._tool_schema_hash is None
    model._cancelled.set()
    with pytest.raises(RuntimeError, match='cancelled'):
        model.reset_context()


def test_compaction_status_preserves_billing_and_marks_estimate():
    status = ContextStatus()
    status.update_usage({'totalTokens': 1234567}, provider=True)
    status.update({'usage': 12345, 'capacity': 200000, 'estimated': True})
    assert status.estimated and 'Token 1.2M' in status.render()
    assert '上下文≈12.3K / 200K' in status.render()


@pytest.mark.parametrize('boundary', ['usage', 'tool'])
def test_app_server_propagates_cooperative_yield_without_tool_error_reply(boundary):
    from ctfbot.model_adapters.codex_app_server import CodexAppServer
    class Server(CodexAppServer):
        def __init__(self): self.sent = []
        def request(self, method, params, *, timeout):
            return {'turn': {'id': 'turn'}}
        def _take_dynamic_turn_message(self, thread, turn, timeout):
            if boundary == 'usage':
                return {'method': 'thread/tokenUsage/updated', 'params': {
                    'threadId': thread, 'turnId': turn, 'tokenUsage': {'last': {'inputTokens': 160001}, 'modelContextWindow': 200000}}}
            return {'method': 'item/tool/call', 'id': 1, 'params': {
                'threadId': thread, 'turnId': turn, 'tool': 'command_run', 'arguments': {}}}
        def _send(self, value): self.sent.append(value)
    def yield_context(_):
        raise ContextCompactionRequired()
    server = Server()
    with pytest.raises(ContextCompactionRequired):
        server.run_dynamic_turn('thread', 'model', 'prompt', [], yield_context, on_event=yield_context)
    assert not server.sent
