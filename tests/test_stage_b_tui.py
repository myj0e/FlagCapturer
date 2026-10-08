from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
from pathlib import Path

from textual.app import App
from textual.widgets import Input, Static

from ctfbot.agent.loop import RunLimits
from ctfbot.application.service import LocalChallengeService
from ctfbot.model_adapters.protocol import FakeModelSession, ScriptedTurn, ToolCall
from ctfbot.runtime.docker import CommandResult
from ctfbot.tui.app import CTFBotApp
from ctfbot.tui.safe_text import safe_plain_text


IMAGE = "sha256:" + "c" * 64
CANDIDATE = "CTFBOT_SYNTHETIC{tui_secret_value}"
REJECTED_CANDIDATE = "CTFBOT_SYNTHETIC{tui_wrong_value}"


def make_workspace(root: Path, *, authorized: bool = True) -> tuple[Path, Path]:
    workspace = root / "workspace"
    input_root = workspace / "input"
    input_root.mkdir(parents=True, mode=0o700)
    os.chmod(workspace, 0o700)
    payload = b"TUI synthetic attachment"
    (input_root / "cipher.bin").write_bytes(payload)
    os.chmod(input_root / "cipher.bin", 0o444)
    os.chmod(input_root, 0o555)
    task = "Analyze this synthetic TUI attachment.\n"
    (workspace / "TASK.md").write_text(task, encoding="utf-8")
    os.chmod(workspace / "TASK.md", 0o444)
    metadata_hash = hashlib.sha256(b"TUI synthetic metadata").hexdigest()
    provenance = {
        "challenge_id": "stage-b-tui",
        "source_commit": "synthetic-commit",
        "challenge_metadata_sha256": metadata_hash,
        "category": "crypto",
        "formal_admission": "admitted",
        "import_mode": "static_files_only",
        "authorization_scope": "private local evaluation; do not redistribute challenge assets",
        "model_data_authorized": authorized,
        "model_data_authorization_basis": "synthetic TUI test" if authorized else None,
        "task_sha256": hashlib.sha256(task.encode("utf-8")).hexdigest(),
        "input_files": [{
            "path": "cipher.bin",
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }],
    }
    (workspace / "provenance.json").write_text(json.dumps(provenance), encoding="utf-8")
    os.chmod(workspace / "provenance.json", 0o444)
    private = root / "private"
    private.mkdir(mode=0o700)
    oracle = private / "oracle.json"
    oracle.write_text(json.dumps({
        "challenge_id": "stage-b-tui",
        "source_commit": "synthetic-commit",
        "challenge_metadata_sha256": metadata_hash,
        "flag": CANDIDATE,
    }), encoding="utf-8")
    os.chmod(oracle, 0o600)
    return workspace, oracle


class QuietRuntime:
    def __init__(self, challenge_root: Path, image: str) -> None:
        self.challenge_root = challenge_root
        self.image = image
        self.closed = False

    def __enter__(self) -> QuietRuntime:
        return self

    def __exit__(self, *_: object) -> None:
        self.closed = True

    def execute(self, argv: list[str], *, timeout: float) -> CommandResult:
        del argv, timeout
        return CommandResult(0, b"", b"")


def make_service(model_factory, runtime_factory=None) -> LocalChallengeService:
    return LocalChallengeService(
        model_factory=model_factory,
        model_metadata={"provider": "fake", "model": "synthetic"},
        runtime_factory=runtime_factory or (lambda root, image: QuietRuntime(root, image)),
    )


def fill_fields(app: CTFBotApp, workspace: Path, oracle: Path, runs_root: Path) -> None:
    app.query_one("#workspace-path", Input).value = str(workspace)
    app.query_one("#oracle-path", Input).value = str(oracle)
    app.query_one("#runtime-image", Input).value = IMAGE
    app.query_one("#runs-root", Input).value = str(runs_root)


def static_text(app: App, selector: str) -> str:
    if selector == "#status":
        return app.status_text
    if selector == "#preview-details":
        return app._preview_text
    widget = app.query_one(selector, Static)
    return str(widget._content)


def test_safe_plain_text_makes_terminal_and_bidi_controls_visible() -> None:
    rendered = safe_plain_text("line\n\t\x1b[2J\x07\u202e")

    assert "\n" not in rendered and "\t" not in rendered and "\x1b" not in rendered
    assert r"\u000a" in rendered and r"\u0009" in rendered
    assert r"\u001b" in rendered and r"\u0007" in rendered and r"\u202e" in rendered


def test_candidate_reader_rejects_traversal_and_hash_mismatch(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    artifact_dir = run_dir / "artifacts"
    artifact_dir.mkdir(parents=True, mode=0o700)
    os.chmod(run_dir, 0o700)
    os.chmod(artifact_dir, 0o700)
    outside = tmp_path / "secret.bin"
    outside.write_text("must not be displayed", encoding="utf-8")
    event = {
        "event_type": "tool_result",
        "name": "candidate_submit",
        "result": {
            "candidate_evidence": {
                "artifact": "artifacts/../../secret.bin",
                "sha256": hashlib.sha256(b"must not be displayed").hexdigest(),
                "bytes": len(b"must not be displayed"),
            }
        },
    }

    assert CTFBotApp._read_candidate_value(event, run_dir) is None

    expected_digest = "0" * 64
    artifact_path = artifact_dir / f"sha256-{expected_digest}.bin"
    artifact_path.write_bytes(b"tampered")
    os.chmod(artifact_path, 0o600)
    event["result"]["candidate_evidence"] = {
        "artifact": f"artifacts/sha256-{expected_digest}.bin",
        "sha256": expected_digest,
        "bytes": len(b"tampered"),
    }
    assert CTFBotApp._read_candidate_value(event, run_dir) is None


def test_dynamic_status_and_event_text_are_escaped_before_rendering(tmp_path: Path) -> None:
    asyncio.run(_dynamic_status_and_event_text_are_escaped_before_rendering(tmp_path))


async def _dynamic_status_and_event_text_are_escaped_before_rendering(tmp_path: Path) -> None:
    app = CTFBotApp(service=make_service(lambda: None), runtime_image=IMAGE, runs_root=tmp_path / "runs")

    async with app.run_test(size=(80, 24)):
        app._set_status("status\x1b]52;c;clipboard\x07\u202e")
        app._render_event({"event_type": "tool_call", "tool_index": 1, "name": "tool\x1b[2J\u202e"})

        assert "\x1b" not in app.status_text and "\x07" not in app.status_text
        assert r"\u001b" in app.status_text and r"\u0007" in app.status_text and r"\u202e" in app.status_text
        assert "\x1b" not in "\n".join(app.displayed_events)
        assert r"\u001b" in "\n".join(app.displayed_events)
        app.exit()


def test_input_fields_escape_control_and_bidi_text_before_rendering(tmp_path: Path) -> None:
    asyncio.run(_input_fields_escape_control_and_bidi_text_before_rendering(tmp_path))


async def _input_fields_escape_control_and_bidi_text_before_rendering(tmp_path: Path) -> None:
    app = CTFBotApp(service=make_service(lambda: None), runtime_image=IMAGE, runs_root=tmp_path / "runs")

    async with app.run_test(size=(80, 24)):
        field = app.query_one("#workspace-path", Input)
        field.value = "/tmp/workspace\x1b[2J\u202e"

        assert "\x1b" not in field._value.plain
        assert "\u202e" not in field._value.plain
        assert r"\u001b" in field._value.plain
        assert r"\u202e" in field._value.plain
        app.exit()


def test_preview_denies_unapproved_transfer_before_run(tmp_path: Path) -> None:
    asyncio.run(_preview_denies_unapproved_transfer_before_run(tmp_path))


async def _preview_denies_unapproved_transfer_before_run(tmp_path: Path) -> None:
    workspace, oracle = make_workspace(tmp_path, authorized=False)
    calls: list[str] = []
    service = make_service(lambda: calls.append("model"), lambda *_: calls.append("runtime"))
    app = CTFBotApp(service=service, runtime_image=IMAGE, runs_root=tmp_path / "runs")

    async with app.run_test(size=(80, 24)) as pilot:
        fill_fields(app, workspace, oracle, tmp_path / "runs")
        await pilot.click("#preview")
        await pilot.pause(0.1)

        assert "NOT AUTHORIZED" in static_text(app, "#preview-details")
        assert app.query_one("#run").disabled is True
        assert calls == []


def test_tui_runs_synthetic_case_shows_evidence_and_generates_report(tmp_path: Path) -> None:
    asyncio.run(_tui_runs_synthetic_case_shows_evidence_and_generates_report(tmp_path))


async def _tui_runs_synthetic_case_shows_evidence_and_generates_report(tmp_path: Path) -> None:
    workspace, oracle = make_workspace(tmp_path)
    model = FakeModelSession((ScriptedTurn(tool_calls=(ToolCall("candidate_submit", {"candidate": CANDIDATE}),)),))
    service = make_service(lambda: model)
    app = CTFBotApp(service=service, runtime_image=IMAGE, runs_root=tmp_path / "runs",
                    limits=RunLimits(max_turns=1, max_tool_calls=2))

    async with app.run_test(size=(80, 24)) as pilot:
        fill_fields(app, workspace, oracle, tmp_path / "runs")
        await pilot.click("#preview")
        await pilot.pause(0.1)
        assert "cipher.bin" in static_text(app, "#preview-details")
        assert app.query_one("#run").disabled is False
        await pilot.click("#run")
        for _ in range(40):
            await pilot.pause(0.1)
            if app.last_result is not None:
                break

        assert app.last_result is not None
        assert app.last_result.verified is True
        assert "verified" in static_text(app, "#status").lower()
        assert CANDIDATE in "\n".join(app.displayed_events)
        assert app.query_one("#evidence").disabled is False
        assert app.query_one("#report").disabled is False

        await pilot.click("#evidence")
        await pilot.pause(0.1)
        assert any("candidate_submit" in line and "verified" in line for line in app.displayed_events)
        assert CANDIDATE in "\n".join(app.displayed_events)
        await pilot.click("#report")
        await pilot.pause(0.1)
        assert app.last_report_path is not None and app.last_report_path.is_file()
        report = app.last_report_path.read_text(encoding="utf-8")
        assert CANDIDATE not in report
        assert "exact-string controller-only" in report
        app.exit()


def test_tui_records_rejected_flag_candidate_without_marking_run_verified(tmp_path: Path) -> None:
    asyncio.run(_tui_records_rejected_flag_candidate_without_marking_run_verified(tmp_path))


async def _tui_records_rejected_flag_candidate_without_marking_run_verified(tmp_path: Path) -> None:
    workspace, oracle = make_workspace(tmp_path)
    model = FakeModelSession((ScriptedTurn(
        tool_calls=(ToolCall("candidate_submit", {"candidate": REJECTED_CANDIDATE}),)
    ),))
    service = make_service(lambda: model)
    app = CTFBotApp(service=service, runtime_image=IMAGE, runs_root=tmp_path / "runs",
                    limits=RunLimits(max_turns=1, max_tool_calls=2))

    async with app.run_test(size=(80, 24)) as pilot:
        fill_fields(app, workspace, oracle, tmp_path / "runs")
        await pilot.click("#preview")
        await pilot.pause(0.1)
        await pilot.click("#run")
        for _ in range(40):
            await pilot.pause(0.1)
            if app.last_result is not None:
                break

        assert app.last_result is not None
        assert app.last_result.verified is False
        assert app.candidate_status == "rejected"
        assert REJECTED_CANDIDATE in "\n".join(app.displayed_events)
        assert "rejected" in "\n".join(app.displayed_events)
        app.exit()


def test_stop_action_cancels_blocked_model_without_freezing_ui(tmp_path: Path) -> None:
    asyncio.run(_stop_action_cancels_blocked_model_without_freezing_ui(tmp_path))


async def _stop_action_cancels_blocked_model_without_freezing_ui(tmp_path: Path) -> None:
    workspace, oracle = make_workspace(tmp_path)
    started = threading.Event()
    released = threading.Event()

    class BlockingModel:
        closed = False

        def run_turn(self, prompt, tools, on_tool_call, *, timeout):
            del prompt, tools, on_tool_call, timeout
            started.set()
            assert released.wait(5)
            raise RuntimeError("interrupted")

        def cancel(self):
            released.set()

        def close(self):
            self.closed = True

    model = BlockingModel()
    service = make_service(lambda: model)
    app = CTFBotApp(service=service, runtime_image=IMAGE, runs_root=tmp_path / "runs",
                    limits=RunLimits(max_turns=1, max_tool_calls=2))

    async with app.run_test(size=(80, 24)) as pilot:
        fill_fields(app, workspace, oracle, tmp_path / "runs")
        await pilot.click("#preview")
        await pilot.pause(0.1)
        assert app.query_one("#run").disabled is False, app.status_text
        await pilot.click("#run")
        assert await asyncio_wait(started, pilot)
        await pilot.click("#stop")
        for _ in range(40):
            await pilot.pause(0.1)
            if app.last_result is not None:
                break

        assert app.last_result is not None
        assert app.last_result.status == "user_cancelled"
        assert model.closed is True
        assert "stopped" in static_text(app, "#status").lower()
        app.exit()


def test_provider_error_event_does_not_display_submitted_candidate(tmp_path: Path) -> None:
    asyncio.run(_provider_error_event_does_not_display_submitted_candidate(tmp_path))


async def _provider_error_event_does_not_display_submitted_candidate(tmp_path: Path) -> None:
    workspace, oracle = make_workspace(tmp_path)
    leak_candidate = "CTFBOT_SYNTHETIC{candidate_must_stay_private}"

    class FailingModel:
        def run_turn(self, prompt, tools, on_tool_call, *, timeout):
            del prompt, tools, timeout
            on_tool_call(ToolCall("candidate_submit", {"candidate": leak_candidate}))
            raise RuntimeError(f"provider failed after submitting {leak_candidate}")

        def close(self) -> None:
            pass

    service = make_service(lambda: FailingModel())
    app = CTFBotApp(service=service, runtime_image=IMAGE, runs_root=tmp_path / "runs",
                    limits=RunLimits(max_turns=1, max_tool_calls=2))

    async with app.run_test(size=(80, 24)) as pilot:
        fill_fields(app, workspace, oracle, tmp_path / "runs")
        await pilot.click("#preview")
        await pilot.pause(0.1)
        await pilot.click("#run")
        for _ in range(40):
            await pilot.pause(0.1)
            if app.last_result is not None:
                break

        assert app.last_result is not None
        assert app.last_result.status == "provider_error"
        displayed = "\n".join(app.displayed_events) + app.status_text
        assert displayed.count(leak_candidate) == 1
        provider_errors = [line for line in app.displayed_events if "RuntimeError" in line]
        assert provider_errors and all("provider failed after submitting" not in line for line in provider_errors)
        app.exit()


async def asyncio_wait(event: threading.Event, pilot) -> bool:
    for _ in range(30):
        if event.is_set():
            return True
        await pilot.pause(0.05)
    return event.is_set()
