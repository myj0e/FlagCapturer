from __future__ import annotations

import asyncio
import threading
from unittest.mock import Mock

import pytest
from textual.app import App, ComposeResult
from textual.events import Paste
from textual.widgets import Input, TextArea

from ctfbot.tui.clipboard import CopyResult
from ctfbot.tui.candidates import CandidateScreen, FlagCandidate
from ctfbot.tui.safe_text import SafePromptArea


class EditorApp(App):
    BINDINGS = [('ctrl+c', 'interrupt', 'Existing behavior')]
    interrupts = 0

    def compose(self) -> ComposeResult:
        yield SafePromptArea('alpha beta', id='editor')
        yield Input(id='next')

    def action_interrupt(self):
        self.interrupts += 1


@pytest.fixture
def clipboard_backend(monkeypatch):
    copies = []
    def copy(value, terminal):
        copies.append(value)
        terminal(value)
        return CopyResult('已复制选区', True)
    monkeypatch.setattr('ctfbot.tui.clipboard_area.copy_text', copy)
    return copies


def test_ctrl_c_copies_selected_text_and_keeps_unselected_behavior(clipboard_backend):
    async def scenario():
        app = EditorApp()
        async with app.run_test() as pilot:
            editor = app.query_one('#editor', TextArea)
            editor.focus()
            await pilot.press('home', 'shift+right', 'shift+right', 'ctrl+c')
            await pilot.pause()
            assert app.clipboard == 'al' and clipboard_backend == ['al']
            assert app.interrupts == 0
            await pilot.press('right', 'ctrl+c')
            assert app.interrupts == 1 and app.clipboard == 'al'
    asyncio.run(scenario())


def test_ctrl_v_reads_system_clipboard_preserves_lines_and_can_undo(monkeypatch, clipboard_backend):
    external = Mock(return_value='外部\n\t提示\x1b\u202e')
    monkeypatch.setattr('ctfbot.tui.clipboard_area.read_text', external)
    async def scenario():
        app = EditorApp()
        async with app.run_test() as pilot:
            editor = app.query_one('#editor', TextArea)
            editor.focus()
            editor.action_select_all()
            await pilot.press('ctrl+v')
            await pilot.pause()
            assert editor.text == '外部\n\t提示\\u001b\\u202e'
            external.assert_called_once()
            await pilot.press('ctrl+z')
            assert editor.text == 'alpha beta'
    asyncio.run(scenario())


def test_ctrl_v_falls_back_to_internal_copy_but_empty_system_text_is_noop(monkeypatch, clipboard_backend):
    external = Mock(side_effect=[None, ''])
    monkeypatch.setattr('ctfbot.tui.clipboard_area.read_text', external)
    async def scenario():
        app = EditorApp()
        async with app.run_test() as pilot:
            editor = app.query_one('#editor', TextArea)
            editor.focus()
            app.copy_to_clipboard('internal\ntext')
            editor.action_select_all()
            await pilot.press('ctrl+v')
            await pilot.pause()
            assert editor.text == 'internal\ntext'
            editor.action_select_all()
            await pilot.press('ctrl+v')
            await pilot.pause()
            assert editor.text == 'internal\ntext'
    asyncio.run(scenario())


@pytest.mark.parametrize('interruption', ['terminal', 'edit', 'focus'])
def test_pending_read_cannot_duplicate_terminal_paste_or_insert_at_stale_position(monkeypatch, clipboard_backend, interruption):
    gate, started, finished = threading.Event(), threading.Event(), threading.Event()
    def read():
        started.set()
        gate.wait(3)
        finished.set()
        return 'native'
    monkeypatch.setattr('ctfbot.tui.clipboard_area.read_text', read)
    async def scenario():
        app = EditorApp()
        async with app.run_test() as pilot:
            editor = app.query_one('#editor', TextArea)
            editor.focus()
            editor.move_cursor((0, len(editor.text)))
            await pilot.press('ctrl+v')
            assert started.is_set()
            if interruption == 'terminal':
                editor.post_message(Paste('terminal\ncontent'))
            elif interruption == 'edit':
                await pilot.press('!')
            else:
                await pilot.press('tab')
            await pilot.pause()
            expected = editor.text
            gate.set()
            await pilot.pause()
            assert finished.is_set() and editor.text == expected
            if interruption == 'terminal':
                assert editor.text == 'alpha betaterminal\ncontent'
    try:
        asyncio.run(scenario())
    finally:
        gate.set()


def test_immediate_copy_then_paste_uses_new_selection_while_native_copy_is_pending(monkeypatch):
    gate = threading.Event()
    def copy(value, terminal):
        gate.wait(3)
        return CopyResult('已复制', True)
    monkeypatch.setattr('ctfbot.tui.clipboard_area.copy_text', copy)
    read = Mock(side_effect=AssertionError('must not read the old OS clipboard'))
    monkeypatch.setattr('ctfbot.tui.clipboard_area.read_text', read)
    async def scenario():
        app = EditorApp()
        async with app.run_test() as pilot:
            editor = app.query_one('#editor', TextArea)
            editor.focus()
            editor.action_select_all()
            await pilot.press('ctrl+c', 'right', 'ctrl+v')
            assert editor.text == 'alpha betaalpha beta'
            read.assert_not_called()
            gate.set()
            await pilot.pause()
    try:
        asyncio.run(scenario())
    finally:
        gate.set()


def test_candidate_ctrl_c_copies_selection_and_readonly_rejects_paste(monkeypatch, clipboard_backend):
    read = Mock(side_effect=AssertionError('readonly text must not read the clipboard'))
    monkeypatch.setattr('ctfbot.tui.clipboard_area.read_text', read)
    async def scenario():
        app = App()
        async with app.run_test() as pilot:
            app.push_screen(CandidateScreen((FlagCandidate('candidate-1', 'flag{answer}', 'unverified'),)))
            await pilot.pause()
            editor = app.screen.query_one('#candidate-value', TextArea)
            editor.focus()
            editor.action_select_all()
            await pilot.press('ctrl+c')
            await pilot.pause()
            assert app.clipboard == 'flag{answer}' and clipboard_backend == ['flag{answer}']
            await pilot.press('ctrl+v')
            editor.post_message(Paste('cannot replace'))
            await pilot.pause()
            assert editor.text == 'flag{answer}'
            read.assert_not_called()
    asyncio.run(scenario())


def test_cut_remains_compatible_with_system_clipboard_paste(monkeypatch, clipboard_backend):
    monkeypatch.setattr('ctfbot.tui.clipboard_area.read_text', lambda: clipboard_backend[-1])
    async def scenario():
        app = EditorApp()
        async with app.run_test() as pilot:
            editor = app.query_one('#editor', TextArea)
            editor.focus()
            editor.action_select_all()
            await pilot.press('ctrl+x')
            await pilot.pause()
            assert editor.text == '' and clipboard_backend == ['alpha beta']
            await pilot.press('ctrl+v')
            await pilot.pause()
            assert editor.text == 'alpha beta'
    asyncio.run(scenario())
