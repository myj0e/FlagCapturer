"""Completion and agent-authored writeup persistence/rendering contracts."""
from __future__ import annotations

import asyncio
import json
import stat

import pytest
from textual.widgets import Markdown

from ctfbot.agent.loop import AgentLoop
from ctfbot.application.control import RunControl
from ctfbot.model_adapters.protocol import FakeModelSession, ScriptedTurn, ToolCall, TurnResult
from ctfbot.reporting.writeup import MAX_WRITEUP_BYTES
from ctfbot.tui.app import CTFBotApp
from test_agent_reliability import registry
from test_stage_b_tui import IMAGE, fill_fields, make_service, make_workspace


WRITEUP = """# 自建题目解题记录

## 题目与线索

读取授权附件，观测来源：`observation-1`。

## 解题步骤

```python
print("flag{authored}")
```

| 子题 | 提交结果 |
| --- | --- |
| 第一问 | flag{authored} |

## 结果

Flag 已提交，任务完成。没有平台验证待办。
"""


def complete_args(**updates):
    return {'outcome': 'completed', 'candidate_ids': ['candidate-1'], 'summary': 'Flag 已提交',
            'unresolved': [], 'writeup': WRITEUP, **updates}


def submit(tools):
    outcome = tools.invoke(ToolCall('candidate_submit', {'candidate': 'flag{authored}'}))
    assert outcome.reply.success
    assert outcome.result['task_status'] == 'completed'
    assert outcome.result['subtask_completed']
    assert not outcome.result['platform_verification_required']
    return outcome


def test_completion_saves_exact_private_markdown_and_evidence_before_finishing(tmp_path):
    tools = registry(tmp_path)
    model = FakeModelSession([ScriptedTurn(tool_calls=(
        ToolCall('candidate_submit', {'candidate': 'flag{authored}'}),
        ToolCall('run_complete', complete_args()),
    ))])
    result = AgentLoop(model, tools, tools.evidence).run('Recover the single requested flag.')
    assert result.status == 'completed'
    path = tools.evidence.run_dir / 'writeup.md'
    assert path.read_text() == WRITEUP
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    completion = tools.reliability.completion
    assert completion['unresolved'] == [] and not completion['platform_verification_required']
    persisted, _ = tools.evidence.read_artifact(completion['writeup_evidence']['artifact'])
    assert persisted == path.read_bytes()
    events = [json.loads(line) for line in tools.evidence.events_path.read_text().splitlines()]
    kinds = [event['event_type'] for event in events]
    assert kinds.index('candidate_recorded') < kinds.index('writeup_saved') < kinds.index('completion_recorded') < kinds.index('run_finished')


@pytest.mark.parametrize('writeup', [None, '', '  \n', '中' * (MAX_WRITEUP_BYTES // 3 + 1)])
def test_missing_empty_or_oversized_writeup_does_not_complete(tmp_path, writeup):
    tools = registry(tmp_path)
    submit(tools)
    args = complete_args(writeup=writeup)
    if writeup is None:
        del args['writeup']
    outcome = tools.invoke(ToolCall('run_complete', args))
    assert not outcome.reply.success and tools.reliability.completion is None
    assert not (tools.evidence.run_dir / 'writeup.md').exists()
    retry = tools.invoke(ToolCall('run_complete', complete_args()))
    assert retry.reply.success and (tools.evidence.run_dir / 'writeup.md').read_text() == WRITEUP


def test_disk_failure_does_not_commit_completion_and_can_retry(tmp_path, monkeypatch):
    tools = registry(tmp_path)
    submit(tools)
    with monkeypatch.context() as context:
        def fail(*args):
            raise OSError('authored disk failure')
        context.setattr('ctfbot.reporting.writeup.os.replace', fail)
        outcome = tools.invoke(ToolCall('run_complete', complete_args()))
        assert not outcome.reply.success and tools.reliability.completion is None
    assert not (tools.evidence.run_dir / 'writeup.md').exists()
    assert not list(tools.evidence.run_dir.glob('.writeup-*'))
    assert tools.invoke(ToolCall('run_complete', complete_args())).reply.success


def test_writeup_destination_symlink_is_rejected(tmp_path):
    tools = registry(tmp_path)
    submit(tools)
    outside = tmp_path / 'outside.md'
    outside.write_text('keep this content')
    (tools.evidence.run_dir / 'writeup.md').symlink_to(outside)
    outcome = tools.invoke(ToolCall('run_complete', complete_args()))
    assert not outcome.reply.success and tools.reliability.completion is None
    assert outside.read_text() == 'keep this content'


def test_completed_and_partial_refer_only_to_requested_subtasks(tmp_path):
    tools = registry(tmp_path)
    submit(tools)
    args = complete_args(unresolved=['second requested flag not recovered'])
    assert not tools.invoke(ToolCall('run_complete', args)).reply.success
    outcome = tools.invoke(ToolCall('run_complete', {**args, 'outcome': 'partial'}))
    assert outcome.reply.success and outcome.result['outcome'] == 'partial'
    original = (tools.evidence.run_dir / 'writeup.md').read_bytes()
    repeated = tools.invoke(ToolCall('run_complete', complete_args(writeup='# Replacement')))
    assert repeated.result['status'] == 'already_completed'
    assert (tools.evidence.run_dir / 'writeup.md').read_bytes() == original


def test_unsolved_attempt_also_persists_agent_writeup(tmp_path):
    tools = registry(tmp_path)
    outcome = tools.invoke(ToolCall('run_complete', complete_args(
        outcome='unsolved', candidate_ids=[], summary='未找到结果', unresolved=['未知编码'],
        writeup='# 未解出记录\n\n尝试读取附件，尚未确定编码，没有编造 Flag。')))
    assert outcome.reply.success and outcome.result['outcome'] == 'unsolved'
    assert (tools.evidence.run_dir / 'writeup.md').is_file()


def test_cancellation_preserves_submitted_flag_without_fabricating_writeup(tmp_path):
    tools = registry(tmp_path)
    control = RunControl()
    class CancelledModel:
        def run_turn(self, prompt, specs, dispatch, *, timeout):
            dispatch(ToolCall('candidate_submit', {'candidate': 'flag{authored}'}))
            control.cancel()
            return TurnResult('Stopped before writing the writeup.', tool_calls=1)
        def close(self):
            pass
    result = AgentLoop(CancelledModel(), tools, tools.evidence, control=control).run('One requested flag.')
    assert result.status == 'user_cancelled' and result.candidate_ids == ('candidate-1',)
    assert not (tools.evidence.run_dir / 'writeup.md').exists()


def test_writeup_tab_renders_saved_file_and_resets_for_next_task(tmp_path):
    asyncio.run(_writeup_tab_renders_saved_file_and_resets_for_next_task(tmp_path))


async def _writeup_tab_renders_saved_file_and_resets_for_next_task(tmp_path):
    model = FakeModelSession([ScriptedTurn(tool_calls=(
        ToolCall('candidate_submit', {'candidate': 'flag{authored}'}),
        ToolCall('run_complete', complete_args()),
    ))])
    app = CTFBotApp(service=make_service(lambda: model), runtime_image=IMAGE)
    async with app.run_test(size=(120, 40)) as pilot:
        fill_fields(app, make_workspace(tmp_path), tmp_path / 'runs')
        assert app.query_one('#writeup').disabled
        await pilot.click('#preview')
        await pilot.click('#run')
        for _ in range(50):
            await pilot.pause(.1)
            if app.last_result:
                break
        assert app.last_result and app.last_result.status == 'completed'
        assert '无需等待平台验证' in app.status_text
        assert not app.query_one('#writeup').disabled
        path = app._run_evidence_dir / 'writeup.md'
        assert path.read_text() == WRITEUP
        await pilot.click('#writeup')
        await pilot.pause()
        assert app.query_one('#run-document').border_title == 'WRITEUP / 解题总结'
        assert not app.query_one('#run-panels').display
        markdown = app.query_one('#report-content', Markdown)
        assert markdown.query('MarkdownH1') and markdown.query('MarkdownFence') and markdown.query('MarkdownTable')
        await pilot.click('#report')
        await pilot.pause()
        assert app.query_one('#run-document').border_title == 'REPORT / 运行报告'
        await pilot.click('#writeup')
        await pilot.press('f2')
        await pilot.pause()
        assert app.query_one('#run-panels').display
        await pilot.click('#preview')
        await pilot.pause()
        assert app.query_one('#writeup').disabled
        assert not app.query_one('#run-document').display
        assert path.read_text() == WRITEUP
        app.exit()
