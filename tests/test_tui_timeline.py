from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace

from textual.widgets import Input

from ctfbot.agent.loop import RunLimits
from ctfbot.model_adapters.protocol import FakeModelSession, ToolCall, TurnResult
from ctfbot.model_adapters.codex_session import CodexModelSession
from ctfbot.tools.registry import CommandResult
from ctfbot.tui.app import CTFBotApp
from ctfbot.tui.timeline import clip, compact_event
from test_stage_b_tui import CANDIDATE, IMAGE, QuietRuntime, make_service, make_workspace


def test_timeline_filters_noise_and_bounds_output():
    for name in ('run_metadata', 'command_execution', 'workflow_read', 'domain_routing', 'run_state_changed'):
        assert compact_event({'event_type': name}, lambda _: None) is None
    event = {'event_type': 'tool_result', 'name': 'command_run', 'result': {'status': 'ok', 'exit_code': 0},
             'response_evidence': {}}
    line = compact_event(event, lambda _: json.dumps({'stdout': 'line\n'*100, 'stderr': ''}))
    assert 'exit=0' in line and '已省略' in line and len(line) < 1100
    safe = clip('\x1b[31m unsafe\x07\u202e')
    assert '\x1b' not in safe and '\x07' not in safe and '\u202e' not in safe
    assert '\\u001b' in safe
    script = compact_event({'event_type': 'tool_call', 'name': 'script_run', 'arguments_artifact': {}},
                           lambda _: '{"path":"solve.py","argv":[]}')
    assert 'solve.py' in script
    background = compact_event({'event_type': 'tool_call', 'name': 'script_start', 'arguments_artifact': {}},
                               lambda _: '{"path":"long.py","argv":["batch-2"]}')
    assert 'long.py' in background and 'batch-2' in background
    progress = compact_event({'event_type': 'tool_result', 'name': 'session_read',
                             'result': {'status': 'session_output', 'elapsed_seconds': 140, 'output_idle_seconds': 15},
                             'response_evidence': {}}, lambda _: '{"output":"progress 5/10"}')
    assert '运行 140s' in progress and '无输出 15s' in progress and 'progress 5/10' in progress


def test_codex_session_forwards_public_message_callback_without_starting_real_provider():
    received = []
    class Server:
        def start_dynamic_thread(self, model, cwd, schemas, *, timeout):
            return 'thread'
        def run_dynamic_turn(self, thread, model, prompt, schemas, handle, *, cwd, effort, timeout, on_message):
            on_message('先查看附件。')
            return {'text': '先查看附件。', 'tool_calls': 0}
    session = CodexModelSession.__new__(CodexModelSession)
    session.model = 'synthetic'
    session.effort = None
    session.timeout = 5
    session._closed = False
    session._cancelled = threading.Event()
    session._thread_id = None
    session._temp = SimpleNamespace(name='/unused-synthetic')
    session._server = Server()
    session.turns_started = 0
    session.set_message_handler(received.append)
    result = session.run_turn('prompt', [], lambda _: None, timeout=5)
    assert received == ['先查看附件。'] and result.text == received[0]


def test_rounds_show_public_analysis_tool_arguments_and_returns_with_expanded_log(tmp_path):
    workspace = make_workspace(tmp_path)

    class ObservedModel:
        index = 0
        def set_message_handler(self, handler):
            self.message = handler
        def run_turn(self, prompt, tools, on_tool_call, *, timeout):
            self.index += 1
            text = '先识别附件结构。' if self.index == 1 else '检查完成，提交候选。'
            self.message(text)
            on_tool_call(ToolCall('command_run', {'argv': ['file', '/challenge/cipher.bin']})) if self.index == 1 else \
                on_tool_call(ToolCall('candidate_submit', {'candidate': CANDIDATE}))
            if self.index == 2:
                on_tool_call(ToolCall('run_complete', {"writeup": '# Authored solution\n\nReproduce the recorded fixture steps; outputs and submitted results are preserved in this run.', 'outcome': 'candidate_unverified', 'candidate_id': 'candidate-1', 'summary': 'Retain candidate', 'unresolved': []}))
            return TurnResult(text=text, tool_calls=1)
        def close(self):
            pass

    class OutputRuntime(QuietRuntime):
        def execute(self, argv, *, timeout):
            return CommandResult(0, b'ELF 64-bit synthetic output\n', b'')

    async def scenario():
        app = CTFBotApp(service=make_service(ObservedModel, OutputRuntime), runtime_image=IMAGE,
                        runs_root=tmp_path/'runs', limits=RunLimits())
        async with app.run_test(size=(100, 36)) as pilot:
            app.query_one('#workspace-path', Input).value = str(workspace)
            await pilot.click('#preview')
            await pilot.pause(.1)
            small_height = app.query_one('#timeline').size.height
            await pilot.click('#run')
            for _ in range(40):
                await pilot.pause(.1)
                if app.last_result:
                    break
            assert app.last_result and app.last_result.status == "candidate_unverified"
            assert app.query_one('#timeline').size.height >= small_height + 6
            assert app.query_one('#summary-panel').display
            assert not app.query_one('#fields').display
            live = list(app.displayed_events)
            text = '\n'.join(live)
            assert '第 1 轮' in text and '第 2 轮' in text
            assert text.count('先识别附件结构。') == 1
            assert text.index('先识别附件结构。') < text.index('工具 1') < text.index('返回 · command_run')
            assert 'file /challenge/cipher.bin' in text and 'ELF 64-bit synthetic output' in text
            assert 'run_metadata' not in text and 'sha256-' not in text
            assert CANDIDATE in text
            await pilot.click('#evidence')
            await pilot.pause(.1)
            assert app.displayed_events == live
            await pilot.press('f2')
            await pilot.pause(.1)
            assert app.query_one('#run-panels').display
            assert not app.query_one('#run-document').display
            assert app.displayed_events == live
            await pilot.press('f2')
            await pilot.pause(.1)
            assert app.query_one('#fields').display
            assert app.query_one('#timeline').size.height == small_height
            await pilot.resize_terminal(80, 24)
            await pilot.pause()
            assert not app.screen.has_class('tall')
            assert app.query_one('#timeline').region.bottom <= 23
            assert app.query_one('#actions').region.bottom < app.query_one('#timeline').region.y
    asyncio.run(scenario())


def test_runtime_tool_count_has_no_limit_denominator(tmp_path):
    async def scenario():
        app = CTFBotApp(service=make_service(lambda: FakeModelSession([])), runtime_image=IMAGE, runs_root=tmp_path/'runs')
        async with app.run_test(size=(100,36)):
            app._run_active = True
            app._render_event({'event_type':'tool_call','name':'challenge_list','tool_index':72})
            assert '工具调用 72 次' in app.status_text
            assert '工具调用 72/' not in app.status_text
            app._run_active = False
    asyncio.run(scenario())


def test_reading_history_stays_paused_for_keyboard_scrollbar_and_new_events(tmp_path):
    from textual.widgets import Button
    from ctfbot.tui.history_log import HistoryLog

    async def scenario():
        app = CTFBotApp(service=make_service(lambda: FakeModelSession([])), runtime_image=IMAGE, runs_root=tmp_path/'runs')
        async with app.run_test(size=(120, 40)) as pilot:
            app._expand_log()
            for turn in range(1, 25):
                app._timeline_state.add({'event_type':'model_turn_started', 'turn':turn}, lambda ref: ref)
                app._timeline_state.add({'event_type':'assistant_message', 'evidence':f'Public message {turn}'}, lambda ref: ref)
            app._refresh_timeline()
            await pilot.pause()
            log = app.query_one('#timeline', HistoryLog)
            assert log.max_scroll_y > 20 and log.scroll_y == log.max_scroll_y
            await pilot.press('pageup')
            await pilot.pause()
            assert not app._follow_timeline
            assert app.query_one('#follow-timeline', Button).has_class('visible')
            position = log.scroll_y
            app._timeline_state.add({'event_type':'model_turn_started', 'turn':25}, lambda ref: ref)
            app._refresh_timeline()
            await pilot.pause()
            assert log.scroll_y == position and not app._follow_timeline
            await pilot.press('end')
            await pilot.pause()
            assert not app._follow_timeline  # Reaching the bottom does not resume.
            await pilot.click('#follow-timeline')
            await pilot.pause()
            assert app._follow_timeline and log.scroll_y == log.max_scroll_y
            # This is also how scrollbar dragging updates the viewport.
            log.scroll_to(y=3, animate=False, immediate=True)
            await pilot.pause()
            assert not app._follow_timeline
            app._refresh_timeline()
            await pilot.pause()
            assert log.scroll_y == 3
    asyncio.run(scenario())


def test_context_updates_statusline_without_filling_history(tmp_path):
    from textual.widgets import Static

    async def scenario():
        app = CTFBotApp(service=make_service(lambda: FakeModelSession([])), runtime_image=IMAGE, runs_root=tmp_path/'runs')
        async with app.run_test(size=(100, 36)):
            app._render_event({'event_type':'provider_initialized', 'provider':{'provider':'codex', 'model':'test-model'}})
            app._render_event({'event_type':'provider_context_usage', 'capacity':200000,
                               'last_inference_usage':{'inputTokens':12000, 'outputTokens':700},
                               'cumulative_usage':{'totalTokens':900000}})
            status = app._context_status.render()
            assert '12.7K / 200K' in status and '≈' in status and 'test-model' in status
            assert 'Token 900K' in status
            assert not app._timeline_state.notices
            assert not any('provider' in line for line in app.displayed_events)
            assert app.query_one('#context-status', Static).region.y > app.query_one('#timeline').region.y
            app._render_event({'event_type':'provider_compaction_started'})
            assert '压缩中' in app._context_status.render()
            app._render_event({'event_type':'provider_compaction_completed'})
            assert '等待用量' in app._context_status.render()
            app._render_event({'event_type':'provider_context_usage', 'capacity':200000,
                               'last_inference_usage':{'totalTokens':6000}})
            assert '6K / 200K' in app._context_status.render()
    asyncio.run(scenario())
