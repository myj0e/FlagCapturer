from __future__ import annotations

import asyncio
from pathlib import Path

from textual.app import App
from textual.widgets import Button, Select, TextArea

from ctfbot.agent.loop import RunLimits
from ctfbot.model_adapters.protocol import FakeModelSession, ScriptedTurn, ToolCall
from ctfbot.tui.app import CTFBotApp
from ctfbot.tui.candidates import CandidateScreen, FlagCandidate
from ctfbot.tui.safe_text import safe_plain_text
from test_stage_b_tui import CANDIDATE, ALTERNATE_CANDIDATE, IMAGE, fill_fields, make_service, make_workspace


def test_completed_run_candidates_can_be_selected_copied_and_reopened(tmp_path: Path):
    workspace = make_workspace(tmp_path)
    service = make_service(lambda: FakeModelSession([
        ScriptedTurn(tool_calls=(ToolCall('candidate_submit', {'candidate': ALTERNATE_CANDIDATE}),
                                 ToolCall('candidate_submit', {'candidate': CANDIDATE}),
                                 ToolCall('run_complete', {'outcome': 'candidate_unverified', 'candidate_id': 'candidate-2', 'summary': 'Retain both candidates', 'unresolved': []})))
    ]))

    async def scenario():
        app = CTFBotApp(service=service, runtime_image=IMAGE, runs_root=tmp_path/'runs', limits=RunLimits(max_turns=1))
        async with app.run_test(size=(120, 40)) as pilot:
            assert app.query_one('#candidates', Button).disabled
            fill_fields(app, workspace, tmp_path/'runs')
            await pilot.click('#preview')
            await pilot.pause()
            await pilot.click('#run')
            for _ in range(40):
                await pilot.pause(.05)
                if app.last_result is not None:
                    break
            assert app.last_result and app.last_result.status == "candidate_unverified"
            assert not app.query_one('#candidates', Button).disabled
            await pilot.click('#candidates')
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, CandidateScreen)
            assert [c.status for c in screen.candidates] == ['unverified', 'unverified']
            assert screen.query_one('#candidate-value', TextArea).text == CANDIDATE
            await pilot.click('#copy-candidate')
            assert app.clipboard == CANDIDATE
            screen.query_one('#candidate-select', Select).value = 0
            await pilot.pause(.4)
            assert screen.selected_index == 0
            assert screen.query_one('#candidate-value', TextArea).text == ALTERNATE_CANDIDATE
            await pilot.click('#copy-candidate')
            assert app.clipboard == ALTERNATE_CANDIDATE
            await pilot.press('escape')
            await pilot.click('#evidence')
            await pilot.pause()
            await pilot.click('#candidates')
            await pilot.pause()
            assert len(app.screen.candidates) == 2  # replay does not duplicate candidates
            await pilot.click('#close-candidates')
            artifact_dir = Path(app.last_result.run_dir)/'artifacts'
            for artifact in artifact_dir.iterdir():
                if artifact.read_bytes() in (CANDIDATE.encode(), ALTERNATE_CANDIDATE.encode()):
                    artifact.write_text('corrupted')
            await pilot.click('#candidates')
            await pilot.pause()
            assert not app.screen.candidates
            assert app.screen.query_one('#copy-candidate', Button).disabled
    asyncio.run(scenario())


def test_clipboard_preserves_full_original_while_display_escapes_controls():
    value = 'SUCTF{' + '中' * 1000 + '}\n\u202e'

    async def scenario():
        app = App()
        async with app.run_test(size=(120, 40)) as pilot:
            app.push_screen(CandidateScreen((FlagCandidate('candidate-1', value, 'unverified'),)))
            await pilot.pause()
            assert app.screen.query_one('#candidate-value', TextArea).text == safe_plain_text(value)
            await pilot.click('#copy-candidate')
            assert app.clipboard == value
            await pilot.press('escape')
    asyncio.run(scenario())
