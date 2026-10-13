"""Final acceptance for manual task stopping and observable token usage."""
from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import asdict
from unittest.mock import patch

import pytest
from textual.widgets import Button, Static

from ctfbot.agent.loop import AgentLoop, RunLimits
from ctfbot.application.service import LocalChallengeService
from ctfbot.model_adapters.codex_app_server import CodexAppServer
from ctfbot.model_adapters.protocol import FakeModelSession, ScriptedTurn, ToolCall, TurnResult
from ctfbot.tui.app import CTFBotApp
from ctfbot.tui.context_status import ContextStatus, format_tokens
from test_agent_reliability import registry
from test_context_management import Runtime, event
from test_multi_flag_progress import eventually
from test_stage_b_tui import IMAGE, QuietRuntime, fill_fields, make_workspace


@pytest.mark.parametrize(('value', 'expected'), [
    (None, '—'), (0, '0'), (999, '999'), (1000, '1K'), (12500, '12.5K'),
    (999999, '1M'), (1000000, '1M'), (1250000, '1.2M'), (123000000, '123M'),
])
def test_token_units_scale_automatically(value, expected):
    assert format_tokens(value) == expected


def test_unfinished_turns_and_long_elapsed_time_continue_until_explicit_completion(tmp_path):
    tools = registry(tmp_path, runtime=Runtime())
    class Continuing(FakeModelSession):
        def run_turn(self, prompt, specs, dispatch, *, timeout):
            assert timeout is None
            self._index += 1
            if self._index < 36:
                # A public response without tools used to silently finish the run.
                return TurnResult('Still solving.', usage={'input_tokens': 1000, 'output_tokens': 100})
            with patch('ctfbot.agent.loop.time.monotonic', return_value=1_000_000_000.0):
                assert dispatch(ToolCall('command_run', {'argv': ['after-old-limits']})).success
            dispatch(ToolCall('run_complete', {"writeup": '# Authored solution\n\nReproduce the recorded fixture steps; outputs and submitted results are preserved in this run.', 'outcome': 'unsolved', 'summary': 'No justified next experiment', 'unresolved': ['unknown key']}))
            return TurnResult('', tool_calls=2, usage={'input_tokens': 1000, 'output_tokens': 100})
    result = AgentLoop(Continuing([]), tools, tools.evidence).run('authored task')
    assert result.turns == 36 and result.status == 'unsolved'
    assert result.stop_reason == 'model_explicit_completion' and tools.runtime.executions == 1
    assert result.usage_totals == {'input_tokens': 36000, 'output_tokens': 3600}
    assert set(asdict(RunLimits())) == {'tool_timeout_seconds', 'per_tool_output_bytes'}
    assert tools.command_timeout == 120
    events = [json.loads(line) for line in tools.evidence.events_path.read_text().splitlines()]
    decisions = [e for e in events if e['event_type'] == 'controller_decision']
    assert len(decisions) == 35 and all(e['action'] == 'continue' and e['idle_turn'] for e in decisions)
    assert not any(e.get('status') == 'budget_exhausted' for e in events)
    assert tools.reliability.budget_feedback['automatic_budget_stop'] is False
    assert 'remaining_wall_seconds' not in tools.reliability.budget_feedback


def test_provider_usage_publishes_immediately_and_deduplicates_across_threads(tmp_path):
    tools = registry(tmp_path)
    loop = AgentLoop(FakeModelSession([]), tools, tools.evidence)
    for thread, total in [('first', 1_000_000), ('first', 1_000_000), ('first', 900_000), ('second', 200_000)]:
        loop.context_events.enqueue({**event('context_usage'), 'thread_id': thread,
            'cumulative_usage': {'inputTokens': total, 'outputTokens': 1000, 'totalTokens': total + 1000},
            'last_inference_usage': {'inputTokens': 10000, 'outputTokens': 1000}, 'capacity': 200000})
    # No tool dispatch / drain / turn completion was needed to emit these events.
    events = [json.loads(line) for line in tools.evidence.events_path.read_text().splitlines()]
    assert len(events) == 4 and all(e['event_type'] == 'provider_context_usage' for e in events)
    assert events[-1]['billing_totals']['totalTokens'] == 1_202_000
    assert loop._usage_snapshot()['totalTokens'] == 1_202_000
    status = ContextStatus()
    for item in events:
        status.update(item)
    assert status.total_tokens == 1_202_000 and status.input_tokens == 1_200_000
    assert 'Token 1.2M' in status.render() and '11K / 200K' in status.render()
    status.update_usage({'total_tokens': 999999999})
    assert status.total_tokens == 1_202_000  # Turn summaries don't double-charge streamed tokens.


def test_usage_fallback_preserves_unknowns_and_survives_compaction():
    status = ContextStatus()
    assert 'Token —' in status.render()
    status.update_usage({'input_tokens': 900000, 'output_tokens': 200000})
    assert status.total_tokens == 1100000 and 'Token 1.1M' in status.render()
    status.update({'capacity': 200000, 'last_inference_usage': {'totalTokens': 210000}})
    assert '105%' in status.render() and 'Token 1.1M' in status.render()
    status.compacting = True
    assert '压缩中' in status.render() and 'Token 1.1M' in status.render()
    status.update({'capacity': 200000, 'last_inference_usage': {'totalTokens': 5000}})
    assert '5K / 200K' in status.render() and 'Token 1.1M' in status.render()


def test_unlimited_provider_turn_keeps_waiting_and_supports_stream_closure():
    # Test the App Server turn path without launching a process or using a model.
    class Server(CodexAppServer):
        def __init__(self):
            self.seen = []
        def request(self, method, params, *, timeout):
            assert method == 'turn/start' and timeout == 30
            return {'turn': {'id': 'u'}}
        def _take_dynamic_turn_message(self, thread, turn, timeout):
            self.seen.append(timeout)
            assert timeout is None
            return {'method': 'turn/completed', 'params': {'threadId': thread, 'turn': {'id': turn, 'status': 'completed'}}}
    server = Server()
    assert server.run_dynamic_turn('t', 'model', 'prompt', [], lambda _: None)['finish_reason'] == 'completed'
    assert server.seen == [None]
    # The mailbox still wakes on cancellation / closed stdout even with no deadline.
    from ctfbot.model_adapters.mailbox import MessageQueue
    from ctfbot.model_adapters.codex_app_server import CodexAppServerError
    server._messages = MessageQueue()
    server._messages.put(None)
    server._stderr_tail = []
    with pytest.raises(CodexAppServerError, match='closed its output stream'):
        server._next_message(None)


def test_live_over_capacity_usage_is_visible_until_manual_stop(tmp_path):
    streamed, release = threading.Event(), threading.Event()
    class Provider(FakeModelSession):
        def set_event_handler(self, handler):
            self.handler = handler
        def run_turn(self, prompt, specs, dispatch, *, timeout):
            assert timeout is None
            self.handler({**event('context_usage'), 'capacity': 200000,
                'last_inference_usage': {'totalTokens': 210000},
                'cumulative_usage': {'inputTokens': 1_100_000, 'outputTokens': 100_000, 'totalTokens': 1_200_000}})
            streamed.set()
            assert release.wait(15)
            raise RuntimeError('manually interrupted')
        def cancel(self):
            release.set()
    model = Provider([])
    service = LocalChallengeService(model_factory=lambda: model, model_metadata={}, runtime_factory=QuietRuntime)
    workspace = make_workspace(tmp_path)
    async def scenario():
        app = CTFBotApp(service=service, runtime_image=IMAGE, runs_root=tmp_path/'runs')
        async with app.run_test(size=(120, 40)) as pilot:
            try:
                fill_fields(app, workspace, tmp_path/'runs')
                await pilot.click('#preview')
                await pilot.pause()
                await pilot.click('#run')
                await eventually(pilot, lambda: streamed.is_set() and app._context_status.total_tokens == 1_200_000)
                assert app._run_active and app.last_result is None and not model.closed
                assert 'Token 1.2M' in app._context_status.render() and '105%' in app._context_status.render()
                assert app.query_one('#context-status', Static).region.height == 1
                assert not app.query_one('#stop', Button).disabled
                assert not any('provider_context_usage' in line for line in app.displayed_events)
                await pilot.click('#stop')
                await eventually(pilot, lambda: app.last_result is not None)
                assert app.last_result.status == 'user_cancelled'
                assert app.last_result.usage_totals['totalTokens'] == 1_200_000
                assert 'Token 1.2M' in app._context_status.render() and model.closed
            finally:
                if app._run_active:
                    app.action_stop()
                release.set()
    asyncio.run(scenario())
