from __future__ import annotations

import asyncio
import json
from unittest.mock import patch
from pathlib import Path

import pytest
from textual.widgets import Button, Input

from ctfbot.agent.loop import RunLimits
from ctfbot.application.baseline import validate_baseline_snapshot
from ctfbot.application.service import LocalChallengeService
from ctfbot.model_adapters.protocol import FakeModelSession, ScriptedTurn, ToolCall
from ctfbot.reporting import generate_basic_report
from ctfbot.reporting.replay import replay_run
from ctfbot.tui.app import CTFBotApp
from test_stage_b_tui import IMAGE, QuietRuntime, make_workspace


def service(turns, runtime=QuietRuntime):
    return LocalChallengeService(model_factory=lambda: FakeModelSession(turns),
                                 model_metadata={"provider": "synthetic"}, runtime_factory=runtime)


def test_candidate_allows_checks_and_explicit_completion_blocks_tools(tmp_path):
    workspace = make_workspace(tmp_path)
    class Commands(QuietRuntime):
        def execute(self, argv, **kwargs):
            assert argv == ['allowed-after-candidate']
            return super().execute(argv, **kwargs)
    model = FakeModelSession([
        ScriptedTurn(tool_calls=(ToolCall("candidate_submit", {"candidate": "wrong"}),
                                 ToolCall("candidate_submit", {"candidate": "flag{unknown}"}),
                                 ToolCall("command_run", {"argv": ["allowed-after-candidate"]}),
                                 ToolCall("run_complete", {"outcome": "candidate_unverified", "candidate_id": "candidate-2", "summary": "Retain an unchecked candidate", "unresolved": ["correctness"]}),
                                 ToolCall("command_run", {"argv": ["must-not-run"]}))),
        ScriptedTurn(text="must not start a second turn"),
    ])
    app_service = LocalChallengeService(model_factory=lambda: model, model_metadata={}, runtime_factory=Commands)
    preview = app_service.preview(workspace, IMAGE, tmp_path/"runs")
    assert preview.start_allowed and preview.additional_prompt == ""
    assert preview.verifier == "model-selected candidate; correctness unverified"
    result = app_service.run(workspace, IMAGE, tmp_path/"runs", RunLimits(max_turns=3))
    assert result.status == "candidate_unverified"
    assert result.turns == 1 and model._index == 1 and model.closed
    events = [json.loads(line) for line in (Path(result.run_dir)/"events.jsonl").read_text().splitlines()]
    statuses = [event["result"]["status"] for event in events if event["event_type"] == "tool_result"]
    assert statuses == ["unverified", "unverified", "ok", "run_complete", "already_completed"]
    report = generate_basic_report(Path(result.run_dir)).read_text()
    assert "candidate_unverified" in report and "correctness unverified" in report
    assert "flag{unknown}" not in report


def test_candidate_workflow_preserves_authorization_and_snapshot_checks(tmp_path):
    workspace = make_workspace(tmp_path, authorized=False)
    app_service = service([])
    assert not app_service.preview(workspace, IMAGE, tmp_path/"runs").start_allowed
    with pytest.raises(ValueError):
        app_service.run(workspace, IMAGE, tmp_path/"runs")
    payload = workspace/"input"/"cipher.bin"
    payload.chmod(0o600)
    payload.write_bytes(b"tampered")
    payload.chmod(0o444)
    with pytest.raises(ValueError): validate_baseline_snapshot(workspace)



def test_candidate_command_run_can_be_replayed(tmp_path):
    workspace = make_workspace(tmp_path)
    app_service = service([ScriptedTurn(tool_calls=(
        ToolCall("command_run", {"argv": ["authored-unit-command"]}),
        ToolCall("candidate_submit", {"candidate": "flag{candidate}"}),
    ))])
    result = app_service.run(workspace, IMAGE, tmp_path/"runs", RunLimits(max_turns=1))
    replay = replay_run(run_dir=Path(result.run_dir), workspace=workspace, output=tmp_path/"replays", runtime_factory=QuietRuntime)
    assert json.loads(replay.read_text())["status"] == "matched"


@pytest.mark.parametrize("candidate", ["SUCTF{answer}", "answer_without_braces", "FLAG{UPPER}", "竞赛{答案}"])
def test_arbitrary_candidates_and_supplemental_hints_reach_model(tmp_path, candidate):
    workspace = make_workspace(tmp_path)
    hint = "保留原始答案前缀，使用附件中的校验逻辑。"
    app_service = service([ScriptedTurn(tool_calls=(ToolCall("candidate_submit", {"candidate": candidate}),))])
    result = app_service.run(workspace, IMAGE, tmp_path/"runs", RunLimits(max_turns=1), additional_prompt=hint)
    run = Path(result.run_dir)
    events = [json.loads(line) for line in (run/"events.jsonl").read_text().splitlines()]
    submission = next(e for e in events if e['event_type'] == 'tool_result' and e['name'] == 'candidate_submit')
    assert submission['result']['status'] == 'unverified'

    texts = [p.read_bytes() for p in (run/'artifacts').rglob('*') if p.is_file()]
    assert any(hint.encode() in text and b'There is no required flag format' in text for text in texts)
    assert any(candidate.encode() == text for text in texts)
    assert 'flag_format' not in json.loads((run/'run.json').read_text())['verification']


def test_cli_solve_reports_candidate_completion(tmp_path, capsys):
    from ctfbot.cli.main import main
    workspace = make_workspace(tmp_path)
    app_service = service([ScriptedTurn(tool_calls=(ToolCall("candidate_submit", {"candidate": "CTF{candidate}"}), ToolCall("run_complete", {"outcome": "candidate_unverified", "candidate_id": "candidate-1", "summary": "Retain candidate", "unresolved": ["competition confirmation"]}),))])
    with patch("ctfbot.cli.main.create_codex_application_service", return_value=app_service):
        status = main(["solve", "--workspace", str(workspace), "--runtime-image", IMAGE,
                       "--runs-root", str(tmp_path/"runs"), "--additional-prompt", "Flag 格式是 CTF{...}", "--confirm-model-usage"])
    assert status == 0
    output = capsys.readouterr().out
    assert "candidate_unverified" in output and "correctness remains unverified" in output
    assert "CTF{candidate}" not in output


def test_tui_has_no_answer_field_and_shows_unconfirmed_candidate(tmp_path):
    workspace = make_workspace(tmp_path)
    async def scenario():
        tui = CTFBotApp(service=service([ScriptedTurn(tool_calls=(ToolCall("candidate_submit", {"candidate": "SUCTF{tui_candidate}"}),
                        ToolCall("run_complete", {"outcome": "candidate_unverified", "candidate_id": "candidate-1", "summary": "Retain candidate", "unresolved": ["correctness"]}))) ]),
                        runtime_image=IMAGE, runs_root=tmp_path/"runs", limits=RunLimits(max_turns=1))
        async with tui.run_test(size=(120, 40)) as pilot:
            tui.query_one("#workspace-path", Input).value = str(workspace)
            assert not tui.query("#oracle-path")
            tui.query_one("#additional-prompt", Input).value = "Flag 可能以 SUCTF 开头"
            await pilot.click("#preview")
            await pilot.pause(.1)
            assert not tui.query_one("#run", Button).disabled
            assert "model-selected candidate" in tui._preview_text
            assert tui._preview.additional_prompt == "Flag 可能以 SUCTF 开头"
            await pilot.click("#run")
            for _ in range(30):
                await pilot.pause(.1)
                if tui.last_result: break
            assert tui.last_result and tui.last_result.status == "candidate_unverified"
            assert "correctness is unverified" in tui.status_text
            assert "SUCTF{tui_candidate}" in "\n".join(tui.displayed_events)
            await pilot.click("#evidence")
            await pilot.pause(.1)
            assert "SUCTF{tui_candidate}" in "\n".join(tui.displayed_events)
            await pilot.click("#report")
            await pilot.pause(.1)
            assert tui.last_report_path and "SUCTF{tui_candidate}" not in tui.last_report_path.read_text()
    asyncio.run(scenario())


def test_removed_answer_option_is_rejected_before_model_start(tmp_path):
    from ctfbot.cli.main import main
    with patch("ctfbot.cli.main.create_codex_application_service") as create_service:
        with pytest.raises(SystemExit) as error:
            main(["solve", "--workspace", str(tmp_path), "--runtime-image", IMAGE,
                  "--oracle", str(tmp_path / "answer.json"), "--confirm-model-usage"])
    assert error.value.code == 2
    create_service.assert_not_called()
