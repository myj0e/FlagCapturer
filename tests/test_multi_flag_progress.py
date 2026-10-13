"""Deterministic contracts for immediate multi-result delivery, not model obedience."""
from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest
from textual.widgets import Button, Select, Static, TextArea
from textual.widgets.text_area import Selection

from ctfbot.agent.loop import AgentLoop, RunLimits
from ctfbot.application.service import LocalChallengeService
from ctfbot.model_adapters.protocol import FakeModelSession, ScriptedTurn, ToolCall, TurnResult
from ctfbot.packs.catalog import COMMON_GUIDANCE
from ctfbot.reporting import generate_basic_report
from ctfbot.reporting.diagnosis import diagnose_run
from ctfbot.tui.app import CTFBotApp
from ctfbot.tui.candidates import CandidateScreen, FlagCandidate
from ctfbot.tui.clipboard import CopyResult
from ctfbot.tui.safe_text import safe_plain_text
from test_agent_reliability import call, registry
from test_stage_b_tui import IMAGE, QuietRuntime, fill_fields, make_workspace


def test_incremental_subtask_contract_and_multi_result_completion(tmp_path):
    executions = []
    class Commands(QuietRuntime):
        def execute(self, argv, **kwargs):
            executions.append(argv)
            return super().execute(argv, **kwargs)
    service = LocalChallengeService(model_factory=lambda: FakeModelSession([
        ScriptedTurn(tool_calls=(
            ToolCall('candidate_submit', {'candidate': '原文{A}', 'subtask': '第一问：恢复密钥'}),
            ToolCall('command_run', {'argv': ['solve-second']}),
            ToolCall('candidate_submit', {'candidate': '原文{B}', 'subtask': '第二阶段'}),
            ToolCall('run_complete', {"writeup": '# Authored solution\n\nReproduce the recorded fixture steps; outputs and submitted results are preserved in this run.', 'outcome': 'candidate_unverified', 'candidate_ids': ['candidate-1', 'candidate-2'],
                                      'summary': 'Two results retained', 'unresolved': []}),
        )),
    ]), model_metadata={}, runtime_factory=Commands)
    result = service.run(make_workspace(tmp_path), IMAGE, tmp_path/'runs')
    assert result.status == 'candidate_unverified' and result.candidate_ids == ('candidate-1', 'candidate-2')
    assert executions == [['solve-second']]
    root = Path(result.run_dir)
    events = [json.loads(line) for line in (root/'events.jsonl').read_text().splitlines()]
    results = [e for e in events if e['event_type'] == 'tool_result']
    assert [e['name'] for e in results] == ['candidate_submit', 'command_run', 'candidate_submit', 'run_complete']
    assert results[0]['result']['ends_run'] is False
    complete = results[-1]['result']
    assert complete['candidate_ids'] == ['candidate-1', 'candidate-2'] and complete['candidate_id'] == 'candidate-1'
    record = next(e for e in events if e['event_type'] == 'candidate_recorded')
    assert record['subtask'] == '第一问：恢复密钥'
    details = json.loads((root/record['details']['artifact']).read_text())
    assert details['subtask'] == record['subtask'] and not details['verified']
    report = generate_basic_report(root).read_text()
    assert 'retained candidates `candidate-1, candidate-2`' in report
    assert '原文{A}' not in report and '原文{B}' not in report
    decision = next(e for e in diagnose_run(root)['controller_decisions'] if e['event_type'] == 'completion_recorded')
    assert decision['candidate_ids'] == ['candidate-1', 'candidate-2']


@pytest.mark.parametrize(('fields', 'outcome', 'expected'), [
    ({'candidate_id': 'candidate-2'}, 'candidate_unverified', ['candidate-2']),
    ({'candidate_ids': ['candidate-2', 'candidate-1', 'candidate-2']}, 'candidate_unverified', ['candidate-2', 'candidate-1']),
    ({'candidate_id': 'candidate-1', 'candidate_ids': ['candidate-2', 'candidate-1']}, 'candidate_unverified', ['candidate-1', 'candidate-2']),
    ({'candidate_ids': []}, 'unsolved', []),
    ({}, 'unsolved', []),
    ({'candidate_ids': []}, 'candidate_unverified', None),
    ({}, 'candidate_unverified', None),
    ({'candidate_id': 'candidate-missing'}, 'unsolved', None),
    ({'candidate_ids': ['candidate-1', 'foreign-run/candidate-1']}, 'candidate_unverified', None),
    ({'candidate_id': 'candidate-missing', 'candidate_ids': ['candidate-1']}, 'candidate_unverified', None),
])
def test_completion_normalizes_owned_ids_and_keeps_legacy_field(tmp_path, fields, outcome, expected):
    tools = registry(tmp_path)
    call(tools, 'candidate_submit', candidate='same', subtask='第一问')
    call(tools, 'candidate_submit', candidate='same', subtask='第二问')
    reply = tools.invoke(ToolCall('run_complete', {"writeup": '# Authored solution\n\nReproduce the recorded fixture steps; outputs and submitted results are preserved in this run.', 'outcome': outcome, 'summary': 'retain results', 'unresolved': [], **fields}))
    assert reply.reply.success == (expected is not None)
    assert len(tools.reliability.candidates) == 2  # No global deduplication by candidate text.
    if expected is None:
        assert tools.reliability.completion is None
    else:
        assert reply.result['candidate_ids'] == expected
        assert reply.result['candidate_id'] == (expected[0] if expected else None)
        assert tools.reliability.completion['candidate_ids'] == expected
        events = [json.loads(line) for line in tools.evidence.events_path.read_text().splitlines()]
        assert next(e for e in events if e['event_type'] == 'completion_recorded')['candidate_ids'] == expected


@pytest.mark.parametrize('label', [None, '中文子题', '中'*160, '第一问\x1b[31m\u202e'])
def test_subtask_is_optional_bounded_and_recoverable(tmp_path, label):
    tools = registry(tmp_path)
    kwargs = {} if label is None else {'subtask': label}
    outcome, payload = call(tools, 'candidate_submit', candidate='flag{value}', **kwargs)
    expected = label or ''
    assert outcome.result['subtask'] == payload['subtask'] == expected
    _, page = call(tools, 'state_read', section='candidates')
    assert page['records'][0]['subtask'] == expected
    snapshot = json.loads(tools.context_state.snapshot(reason='test', constraints={}))
    assert snapshot['candidates'][0]['subtask'] == expected
    assert not tools.invoke(ToolCall('candidate_submit', {'candidate': 'flag{bad}', 'subtask': '中'*161})).reply.success
    assert len(tools.reliability.candidates) == 1


def test_initial_continuation_and_tool_guidance_prioritize_immediate_submission(tmp_path):
    tools = registry(tmp_path)
    loop = AgentLoop(FakeModelSession([]), tools, tools.evidence)
    for prompt in (loop._initial_prompt('two subtasks'), loop._continuation_prompt(), COMMON_GUIDANCE):
        assert 'immediately' in prompt and 'before further experiments' in prompt
        assert 'summary_update' in prompt and 'remaining requested' in prompt
        assert 'unresolved' in prompt and 'candidate_ids' in prompt
    descriptions = {spec.name: spec.description for spec in tools.specs}
    assert 'immediately' in descriptions['candidate_submit'] and 'continue' in descriptions['candidate_submit'].lower()
    assert 'unresolved' in descriptions['run_complete'] and 'candidate_ids' in descriptions['run_complete']


class GatedProvider(FakeModelSession):
    def __init__(self):
        super().__init__([])
        self.allow_a = threading.Event()
        self.allow_b = threading.Event()
        self.allow_finish = threading.Event()
        self.cancelled = False
        self.value_a = '原文{A}\n' + '\n'.join(f'证据行 {i}' for i in range(50)) + '\u202e'

    def cancel(self):
        self.cancelled = True
        self.release()

    def release(self):
        for gate in (self.allow_a, self.allow_b, self.allow_finish):
            gate.set()

    def run_turn(self, prompt, specs, dispatch, *, timeout):
        assert self.allow_a.wait(15)
        if self.cancelled:
            return TurnResult('stopped')
        dispatch(ToolCall('candidate_submit', {'candidate': self.value_a, 'subtask': '第一问\x1b[31m'}))
        dispatch(ToolCall('command_run', {'argv': ['continue-second-subtask']}))
        assert self.allow_b.wait(15)
        if self.cancelled:
            return TurnResult('stopped')
        dispatch(ToolCall('candidate_submit', {'candidate': '原文{B}', 'subtask': '第二问'}))
        assert self.allow_finish.wait(15)
        if not self.cancelled:
            dispatch(ToolCall('run_complete', {"writeup": '# Authored solution\n\nReproduce the recorded fixture steps; outputs and submitted results are preserved in this run.', 'outcome': 'candidate_unverified', 'candidate_ids': ['candidate-1', 'candidate-2'],
                                              'summary': 'retain both', 'unresolved': []}))
        return TurnResult('', tool_calls=4)


async def eventually(pilot, condition):
    for _ in range(150):
        if condition():
            return
        await pilot.pause(.02)
    assert condition(), 'UI state did not reach the controlled synchronization point'


@pytest.mark.parametrize('stop', [False, True])
def test_live_window_keeps_selection_copy_and_background_updates(tmp_path, monkeypatch, stop):
    provider = GatedProvider()
    copy_entered, copy_release = threading.Event(), threading.Event()
    copied = []
    def copy(value, terminal):
        copied.append(value)
        copy_entered.set()
        assert copy_release.wait(15)
        return CopyResult('copied exact original', True)
    monkeypatch.setattr('ctfbot.tui.candidates.copy_text', copy)
    executions = []
    class Commands(QuietRuntime):
        def execute(self, argv, **kwargs):
            executions.append(argv)
            return super().execute(argv, **kwargs)
    service = LocalChallengeService(model_factory=lambda: provider, model_metadata={}, runtime_factory=Commands)
    workspace = make_workspace(tmp_path)

    async def scenario():
        app = CTFBotApp(service=service, runtime_image=IMAGE, runs_root=tmp_path/'runs', limits=RunLimits())
        async with app.run_test(size=(120, 40)) as pilot:
            try:
                fill_fields(app, workspace, tmp_path/'runs')
                await pilot.click('#preview')
                await pilot.pause()
                await pilot.click('#run')
                await eventually(pilot, lambda: bool(app._run_id))
                assert app._run_evidence_dir is not None and app.last_result is None
                assert not app.query_one('#candidates', Button).disabled
                await pilot.click('#candidates')
                await pilot.pause()
                screen = app.screen
                assert isinstance(screen, CandidateScreen) and not screen.candidates
                assert '尚未提交' in str(screen.query_one('#candidate-meta', Static).render())
                provider.allow_a.set()
                await eventually(pilot, lambda: len(screen.candidates) == 1 and bool(executions))
                assert app.last_result is None and app._run_active
                assert executions == [['continue-second-subtask']]
                editor = screen.query_one('#candidate-value', TextArea)
                assert editor.text == safe_plain_text(provider.value_a)
                assert '\x1b' not in str(screen.query_one('#candidate-meta', Static).render())
                editor.selection = Selection((3, 0), (4, 2))
                editor.scroll_end(animate=False, immediate=True)
                await pilot.pause()
                selection, scroll_y = editor.selection, editor.scroll_y
                assert scroll_y > 0
                await pilot.click('#copy-candidate')
                await eventually(pilot, copy_entered.is_set)
                assert app.clipboard == provider.value_a and copied == [provider.value_a]
                app._pause_timeline_follow()
                provider.allow_b.set()
                await eventually(pilot, lambda: len(screen.candidates) == 2)
                assert app.last_result is None and app._run_active
                assert screen.selected_index == 0 and editor.text == safe_plain_text(provider.value_a)
                assert editor.selection == selection and editor.scroll_y == scroll_y
                assert screen.query_one('#copy-candidate', Button).disabled
                assert '正在复制' in str(screen.query_one('#candidate-copy-status', Static).render())
                assert str(app.query_one('#candidates', Button).label) == 'Flag · 2'
                assert len(screen.query_one('#candidate-select', Select)._options) == 2
                assert app.clipboard == provider.value_a and copied == [provider.value_a]
                # Live events still target the main screen while the modal is on top.
                app._render_event({'event_type': 'provider_context_usage', 'run_id': app._run_id,
                                   'capacity': 200000, 'current_context_tokens': 1234})
                assert not app._follow_timeline
                copy_release.set()
                await eventually(pilot, lambda: not screen.query_one('#copy-candidate', Button).disabled)
                await pilot.press('escape')
                assert app.screen is app.default_screen and not app._follow_timeline
                await pilot.click('#candidates')
                await pilot.pause()
                screen = app.screen
                assert len(screen.candidates) == 2 and screen.selected_index == 1
                if stop:
                    app.action_stop()
                else:
                    provider.allow_finish.set()
                await eventually(pilot, lambda: app.last_result is not None)
                assert app.screen is screen and not screen.running and len(screen.candidates) == 2
                assert app.last_result.status == ('user_cancelled' if stop else 'candidate_unverified')
                assert not app._follow_timeline
                await pilot.press('escape')
                await pilot.click('#candidates')
                await pilot.pause()
                assert len(app.screen.candidates) == 2
            finally:
                copy_release.set()
                provider.release()
                if app._run_active:
                    app.action_stop()
    asyncio.run(scenario())


def test_candidate_events_are_owned_deduplicated_and_reset_between_runs(tmp_path):
    from ctfbot.evidence.store import EvidenceStore
    store_a = EvidenceStore(tmp_path/'evidence-a', run_id='run-a')
    store_b = EvidenceStore(tmp_path/'evidence-b', run_id='run-b')
    def submitted(store, value):
        return store.append('tool_result', name='candidate_submit', result={
            'status': 'unverified', 'candidate_id': 'candidate-1',
            'candidate_evidence': store.write_artifact(value.encode()),
        })
    first = submitted(store_a, 'A')
    second = submitted(store_b, 'B')
    workspace = make_workspace(tmp_path)
    service = LocalChallengeService(model_factory=lambda: FakeModelSession([]), model_metadata={}, runtime_factory=QuietRuntime)

    async def scenario():
        app = CTFBotApp(service=service, runtime_image=IMAGE, runs_root=tmp_path/'runs')
        async with app.run_test(size=(120, 40)) as pilot:
            fill_fields(app, workspace, tmp_path/'runs')
            await pilot.click('#preview')
            await pilot.pause()
            app._run_active = True
            generation = app._run_generation
            app._render_event({'event_type': 'run_metadata', 'run_id': 'run-a'}, evidence_dir=store_a.run_dir)
            app._render_event(first, evidence_dir=store_a.run_dir)
            app._render_event(first, evidence_dir=store_a.run_dir)
            app._render_event(second, evidence_dir=store_b.run_dir)
            app._render_event({**first, 'result': {**first['result'], 'candidate_id': 'failed', 'status': 'invalid_arguments'}}, evidence_dir=store_a.run_dir)
            assert list(app._candidates) == ['candidate-1'] and app._candidates['candidate-1'].value == 'A'
            assert app._run_id == 'run-a' and app._run_evidence_dir == store_a.run_dir
            app._run_active = False
            app._preview_challenge()
            assert not app._candidates and not app._run_id
            app._event_queue.put(((generation, 'event'), (first, store_a.run_dir)))
            app._event_queue.put(((generation, 'error'), RuntimeError('stale error')))
            app._drain_events()
            assert not app._candidates and 'stale error' not in app.status_text
            app._run_active = True
            app._render_event({'event_type': 'run_metadata', 'run_id': 'run-b'}, evidence_dir=store_b.run_dir)
            app._render_event(second, evidence_dir=store_b.run_dir)
            app._render_event(first, evidence_dir=store_a.run_dir)
            assert list(app._candidates) == ['candidate-1'] and app._candidates['candidate-1'].value == 'B'
            assert app._run_id == 'run-b'
            app._run_active = False
    asyncio.run(scenario())


def test_appending_keeps_nonfirst_selection_and_text_widget_state():
    from textual.app import App
    candidates = tuple(FlagCandidate(f'candidate-{i}', f'flag{{{i}}}', 'unverified', f'第{i}问') for i in range(1, 4))
    async def scenario():
        app = App()
        async with app.run_test(size=(120, 40)) as pilot:
            screen = CandidateScreen(candidates[:2], running=True)
            app.push_screen(screen)
            await pilot.pause()
            editor = screen.query_one('#candidate-value', TextArea)
            editor.selection = Selection((0, 1), (0, 4))
            selection = editor.selection
            screen.query_one('#candidate-copy-status', Static).update('keep this copy status')
            screen.update_candidates(candidates, running=True)
            await pilot.pause()
            assert screen.selected_index == screen.query_one('#candidate-select', Select).value == 1
            assert editor.text == 'flag{2}' and editor.selection == selection
            assert 'keep this copy status' in str(screen.query_one('#candidate-copy-status', Static).render())
            screen.query_one('#candidate-select', Select).value = 2
            await pilot.pause()
            assert editor.text == 'flag{3}'
    asyncio.run(scenario())


def test_many_candidates_can_be_retained_and_unselected_history_is_preserved(tmp_path):
    tools = registry(tmp_path)
    for i in range(34):
        call(tools, 'candidate_submit', candidate=f'flag{{{i}}}')
    ids = list(tools.reliability.candidates)[:-1]
    _, payload = call(tools, 'run_complete', outcome='candidate_unverified', candidate_ids=ids,
                      summary='retain many results', unresolved=['one discarded hypothesis'], writeup='# Authored solution\n\nReproduce the recorded fixture steps; outputs and submitted results are preserved in this run.')
    assert payload['candidate_ids'] == ids and len(ids) == 33
    assert len(tools.reliability.candidates) == 34 and 'candidate-34' in tools.reliability.candidates


def test_legacy_completion_report_keeps_singular_id(tmp_path):
    from ctfbot.reporting import _timeline_details
    text = _timeline_details({'event_type': 'completion_recorded', 'candidate_id': 'candidate-legacy',
                              'outcome': 'candidate_unverified'})
    assert 'candidate-legacy' in text


def test_candidate_arrival_during_modal_mount_is_not_lost(tmp_path):
    from ctfbot.evidence.store import EvidenceStore
    store = EvidenceStore(tmp_path/'run', run_id='mount-race')
    event = store.append('tool_result', name='candidate_submit', result={
        'status': 'unverified', 'candidate_id': 'candidate-1',
        'candidate_evidence': store.write_artifact(b'flag{during-mount}'),
    })
    async def scenario():
        app = CTFBotApp(runtime_image=IMAGE)
        async with app.run_test(size=(120, 40)) as pilot:
            app._run_active = True
            try:
                app._show_candidates()
                app._render_event(event, evidence_dir=store.run_dir)
                await pilot.pause()
                assert isinstance(app.screen, CandidateScreen)
                assert len(app.screen.candidates) == 1
                assert app.screen.query_one('#candidate-value', TextArea).text == 'flag{during-mount}'
            finally:
                app._run_active = False
    asyncio.run(scenario())
