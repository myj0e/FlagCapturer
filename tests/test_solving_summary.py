from __future__ import annotations

import asyncio
import json
from pathlib import Path

from textual.widgets import Static

from ctfbot.agent.loop import RunLimits
from ctfbot.model_adapters.protocol import FakeModelSession, ScriptedTurn, ToolCall
from ctfbot.tui.app import CTFBotApp
from ctfbot.tui.summary import SolvingSummary
from test_agent_reliability import registry
from test_stage_b_tui import IMAGE, fill_fields, make_service, make_workspace


def snapshot(goal='提取 RSA 参数', **changes):
    return dict(goal=goal, facts=[{'text': '附件包含整数', 'observation_ids': []}],
                hypotheses=['可能是 RSA'], approach='先读取原始参数再选择算法',
                next_steps=['检查模数'], blockers=[], corrections=[], **changes)


def test_summary_is_bounded_source_checked_and_updates_after_progress(tmp_path):
    tools = registry(tmp_path)
    tools.reliability.call_context = {'turn': 1}
    args = snapshot()
    args['facts'][0]['observation_ids'] = ['observation-missing']
    assert not tools.invoke(ToolCall('summary_update', args)).reply.success
    args['facts'][0]['observation_ids'] = []
    first = tools.invoke(ToolCall('summary_update', args))
    assert first.reply.success and first.result['revision'] == 1
    duplicate = tools.invoke(ToolCall('summary_update', snapshot('不得覆盖')))
    assert duplicate.result['status'] == 'summary_already_updated'
    assert tools.reliability.summary['goal'] == args['goal']
    # A provider turn can include many public explanations and tool calls.
    tools.reliability.call_context = {'turn': 1, 'public_message_count': 1}
    assert tools.invoke(ToolCall('summary_update', snapshot('取得新公开说明'))).result['revision'] == 2
    tools.reliability.call_context = {'turn': 2}
    args = snapshot('重新检查输入')
    args['hypotheses'] = ['a'] * 4
    assert not tools.invoke(ToolCall('summary_update', args)).reply.success
    args['hypotheses'] = []
    args['facts'] = []
    args['corrections'] = ['撤回 RSA 假设']
    assert tools.invoke(ToolCall('summary_update', args)).result['revision'] == 3
    assert tools.reliability.summary['facts'] == []
    # New tool evidence permits an update within the same provider turn.
    observed = tools.invoke(ToolCall('challenge_read_bytes', {'path': 'input.txt', 'offset': 0, 'length': 7}))
    oid = json.loads(observed.reply.content)['observation']['id']
    bound = snapshot('已读取附件')
    bound['facts'][0]['observation_ids'] = [oid]
    assert tools.invoke(ToolCall('summary_update', bound)).result['revision'] == 4
    events = [json.loads(line) for line in tools.evidence.events_path.read_text().splitlines()]
    updates = [e for e in events if e['event_type'] == 'summary_updated']
    assert len(updates) == 4
    for event in updates:
        raw, _ = tools.evidence.read_artifact(event['details']['artifact'])
        assert json.loads(raw)['turn'] == event['turn']
        assert not json.loads(raw)['controller_proven']


def test_summary_public_text_controls_and_staleness():
    summary = SolvingSummary()
    data = snapshot('[bold]原文\x1b\u202e')
    data.update(turn=1, revision=1, controller_proven=False)
    assert summary.replace(data)
    summary.turn = 2
    visible = summary.render().plain
    assert '本轮尚未更新' in visible and '未绑定证据' in visible
    assert '[bold]原文' in visible and '\x1b' not in visible and '\u202e' not in visible
    assert not summary.replace(None)
    assert summary.snapshot == data and '保留上一份' in summary.render().plain


def test_dashboard_live_replay_toggle_resize_and_invalid_artifact(tmp_path: Path):
    workspace = make_workspace(tmp_path)
    second = snapshot('检查另一种编码')
    second['facts'] = []
    second['hypotheses'] = ['可能是编码而非加密']
    second['corrections'] = ['撤回 RSA 假设']
    model = FakeModelSession([
        ScriptedTurn(text='先检查参数', tool_calls=(ToolCall('summary_update', snapshot()),)),
        ScriptedTurn(text='需要更换方法', tool_calls=(ToolCall('summary_update', second),)),
        ScriptedTurn(tool_calls=(ToolCall('run_complete', {'outcome': 'unsolved', 'summary': '缺少进一步依据', 'unresolved': ['编码类型']}),)),
    ])

    async def scenario():
        app = CTFBotApp(service=make_service(lambda: model), runtime_image=IMAGE,
                       runs_root=tmp_path/'runs', limits=RunLimits(max_turns=3))
        async with app.run_test(size=(160, 50)) as pilot:
            fill_fields(app, workspace, tmp_path/'runs')
            await pilot.click('#preview')
            await pilot.pause()
            await pilot.click('#run')
            for _ in range(50):
                await pilot.pause(.05)
                if app.last_result:
                    break
            assert app.last_result and app.last_result.status == 'unsolved'
            assert app._solving_summary.snapshot['goal'] == second['goal']
            assert app._solving_summary.turn == 3
            assert app._solving_summary.outcome == 'unsolved'
            assert app._solving_summary.snapshot['facts'] == []
            assert '本轮尚未更新' in app._solving_summary.render().plain
            left = app.query_one('#timeline-panel')
            right = app.query_one('#summary-panel')
            assert 2.8 < left.region.width / right.region.width < 3.2
            await pilot.press('f3')
            assert not right.display
            await pilot.press('f3')
            assert right.display
            await pilot.resize_terminal(90, 50)
            await pilot.pause()
            assert app.query_one('#run-panels').has_class('narrow')
            assert right.region.y >= left.region.bottom
            await pilot.resize_terminal(160, 50)
            await pilot.pause()
            before = app._solving_summary.snapshot.copy()
            await pilot.click('#evidence')
            await pilot.pause()
            assert app._solving_summary.snapshot == before
            assert app._solving_summary.outcome == 'unsolved'
            run = Path(app.last_result.run_dir)
            updates = [json.loads(line) for line in (run/'events.jsonl').read_text().splitlines()
                       if json.loads(line)['event_type'] == 'summary_updated']
            app._follow_timeline = False
            app._render_event({**updates[-1], 'details': {'artifact': '../unsafe'}}, evidence_dir=run)
            assert app._solving_summary.read_error and app._solving_summary.snapshot == before
            assert not app._follow_timeline
            assert app.query_one('#solve-summary', Static)
            # A new preview clears both current and historical dashboard contents.
            await pilot.click('#preview')
            await pilot.pause()
            assert app._solving_summary.snapshot is None
    asyncio.run(scenario())


def test_multiple_public_explanations_in_one_provider_turn_refresh_dashboard(tmp_path):
    from ctfbot.agent.loop import AgentLoop
    from ctfbot.model_adapters.protocol import TurnResult

    class StreamingModel:
        def set_message_handler(self, handler):
            self.message = handler

        def run_turn(self, prompt, tools, on_tool_call, *, timeout):
            self.message('初步方案')
            assert on_tool_call(ToolCall('summary_update', snapshot('初步目标'))).success
            self.message('更正方案')
            reply = on_tool_call(ToolCall('summary_update', snapshot('更正目标')))
            assert json.loads(reply.content)['revision'] == 2
            on_tool_call(ToolCall('run_complete', {'outcome': 'unsolved', 'summary': '尚缺证据', 'unresolved': []}))
            return TurnResult(text='', tool_calls=3)

        def close(self):
            pass

    tools = registry(tmp_path)
    result = AgentLoop(StreamingModel(), tools, tools.evidence).run('合成任务')
    assert result.turns == 1 and result.status == 'unsolved'
    assert tools.reliability.summary['goal'] == '更正目标'
    assert tools.reliability.summary['revision'] == 2
