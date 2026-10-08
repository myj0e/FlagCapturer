from __future__ import annotations

import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from ctfbot.agent.loop import RunLimits
from ctfbot.application.control import RunControl
from ctfbot.application.service import LocalChallengeService
from ctfbot.evidence.store import EvidenceStore
from ctfbot.model_adapters.codex_app_server import CodexAppServer
from ctfbot.model_adapters.protocol import FakeModelSession, ScriptedTurn, ToolCall, TurnResult
from ctfbot.reporting import generate_basic_report
from ctfbot.runtime.docker import DockerRuntime
from ctfbot.runtime.sessions import InteractiveReadResult, InteractiveSession
from ctfbot.tools.registry import CommandResult


IMAGE = "sha256:" + "b" * 64
CANDIDATE = "CTFBOT_SYNTHETIC{stage_b_report_secret}"


def make_workspace(root: Path) -> Path:
    workspace = root / "workspace"
    input_root = workspace / "input"
    input_root.mkdir(parents=True, mode=0o700)
    os.chmod(workspace, 0o700)
    payload = b"report fixture input"
    (input_root / "input.txt").write_bytes(payload)
    os.chmod(input_root / "input.txt", 0o444)
    os.chmod(input_root, 0o555)
    task = "Analyze this synthetic report fixture.\n"
    (workspace / "TASK.md").write_text(task, encoding="utf-8")
    os.chmod(workspace / "TASK.md", 0o444)
    metadata_hash = hashlib.sha256(b"report fixture metadata").hexdigest()
    provenance = {
        "challenge_id": "stage-b-report",
        "source_commit": "synthetic-commit",
        "challenge_metadata_sha256": metadata_hash,
        "category": "forensics",
        "formal_admission": "admitted",
        "import_mode": "static_files_only",
        "authorization_scope": "private local evaluation; do not redistribute challenge assets",
        "model_data_authorized": True,
        "model_data_authorization_basis": "synthetic report test",
        "task_sha256": hashlib.sha256(task.encode("utf-8")).hexdigest(),
        "input_files": [{
            "path": "input.txt",
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }],
    }
    (workspace / "provenance.json").write_text(json.dumps(provenance), encoding="utf-8")
    os.chmod(workspace / "provenance.json", 0o444)
    return workspace


class QuietRuntime:
    def __init__(self, challenge_root: Path, image: str) -> None:
        self.challenge_root = challenge_root
        self.image = image
        self.closed = False

    def __enter__(self) -> QuietRuntime:
        return self

    def __exit__(self, *_: object) -> None:
        self.closed = True

    def close(self) -> None:
        self.closed = True

    def execute(self, argv: list[str], *, timeout: float) -> CommandResult:
        del argv, timeout
        return CommandResult(0, b"", b"")


def make_service(model_factory, runtime_factory):
    return LocalChallengeService(
        model_factory=model_factory,
        model_metadata={"provider": "fake", "model": "synthetic"},
        runtime_factory=runtime_factory,
    )


def run_in_thread(service, workspace: Path, root: Path, control: RunControl, event_sink=None):
    outcome: dict[str, object] = {}

    def target() -> None:
        try:
            outcome["result"] = service.run(
                workspace, IMAGE, root / "runs", RunLimits(max_turns=1, max_tool_calls=2),
                control=control,
                event_sink=event_sink,
            )
        except BaseException as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, outcome


def test_stop_interrupts_blocked_model_turn_and_closes_runtime(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    started = threading.Event()
    release = threading.Event()

    class BlockingModel:
        cancelled = False
        closed = False

        def run_turn(self, prompt, tools, on_tool_call, *, timeout):
            del prompt, tools, on_tool_call, timeout
            started.set()
            assert release.wait(5)
            raise RuntimeError("model process interrupted")

        def cancel(self) -> None:
            self.cancelled = True
            release.set()

        def close(self) -> None:
            self.closed = True

    model = BlockingModel()
    runtimes: list[QuietRuntime] = []

    def make_runtime(root: Path, image: str) -> QuietRuntime:
        runtime = QuietRuntime(root, image)
        runtimes.append(runtime)
        return runtime

    control = RunControl()
    service = make_service(lambda: model, make_runtime)
    thread, outcome = run_in_thread(service, workspace, tmp_path, control)
    assert started.wait(3)

    assert control.cancel() is True
    assert control.cancel() is False
    thread.join(5)

    assert not thread.is_alive()
    assert "error" not in outcome
    result = outcome["result"]
    assert result.status == "user_cancelled"
    assert result.stop_reason == "user_cancelled"
    assert model.cancelled is True
    assert model.closed is True
    assert runtimes[0].closed is True
    assert control.wait_for_callbacks(2) is True
    events = [json.loads(line) for line in (Path(result.run_dir) / "events.jsonl").read_text().splitlines()]
    assert any(event["event_type"] == "run_cancelled" for event in events)


def test_stop_interrupts_blocked_command_and_preserves_cleanup(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    started = threading.Event()
    release = threading.Event()

    class BlockingRuntime(QuietRuntime):
        cancelled = False

        def execute(self, argv: list[str], *, timeout: float) -> CommandResult:
            del argv, timeout
            started.set()
            assert release.wait(5)
            return CommandResult(137, b"", b"stopped")

        def cancel(self) -> None:
            self.cancelled = True
            release.set()

    runtime = BlockingRuntime(workspace / "input", IMAGE)
    model = FakeModelSession((ScriptedTurn(tool_calls=(ToolCall("command_run", {"argv": ["sleep"]}),)),))
    control = RunControl()
    service = make_service(lambda: model, lambda *_: runtime)
    thread, outcome = run_in_thread(service, workspace, tmp_path, control)
    assert started.wait(3)

    control.cancel()
    thread.join(5)

    assert not thread.is_alive()
    assert "error" not in outcome
    result = outcome["result"]
    assert result.status == "user_cancelled"
    assert runtime.cancelled is True
    assert runtime.closed is True
    assert model.closed is True


def test_candidate_result_records_unverified_metadata_and_redacts_report(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    control = RunControl()
    model = FakeModelSession((ScriptedTurn(tool_calls=(ToolCall("candidate_submit", {"candidate": CANDIDATE}), ToolCall("run_complete", {"outcome": "candidate_unverified", "candidate_id": "candidate-1", "summary": "Retain candidate", "unresolved": ["competition confirmation"]}),)),))
    events: list[dict] = []
    service = make_service(lambda: model, lambda root, image: QuietRuntime(root, image))

    result = service.run(
        workspace, IMAGE, tmp_path / "runs", RunLimits(max_turns=1),
        control=control,
        event_sink=events.append,
    )

    report_path = generate_basic_report(Path(result.run_dir))
    report = report_path.read_text(encoding="utf-8")
    candidate_result = next(
        event for event in events
        if event["event_type"] == "tool_result" and event.get("name") == "candidate_submit"
    )
    assert result.status == "candidate_unverified"
    assert candidate_result["result"]["status"] == "unverified"
    assert candidate_result["result"]["verification_method"] == "none"
    assert "stage-b-report" in report
    assert "input.txt" in report
    assert "limits" in report
    assert "verified" in report
    assert "candidate_submit" in report
    assert "artifacts/sha256-" in report
    assert CANDIDATE not in report
    assert "oracle.json" not in report


def test_event_sink_failure_does_not_prevent_persisted_event(tmp_path: Path) -> None:
    def broken_sink(_record: dict) -> None:
        raise RuntimeError("UI closed")

    evidence = EvidenceStore(tmp_path / "run", event_sink=broken_sink)

    event = evidence.append("run_started", status="synthetic")

    persisted = json.loads(evidence.events_path.read_text(encoding="utf-8"))
    assert event["event_type"] == "run_started"
    assert persisted["status"] == "synthetic"


def test_runtime_startup_error_returns_reportable_error_and_closes_resources(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    model = FakeModelSession(())

    class BrokenRuntime(QuietRuntime):
        def __enter__(self):
            raise RuntimeError("synthetic runtime unavailable")

    runtime = BrokenRuntime(workspace / "input", IMAGE)
    service = make_service(lambda: model, lambda *_: runtime)

    result = service.run(workspace, IMAGE, tmp_path / "runs", RunLimits(max_turns=1))

    assert result.status == "error"
    assert result.stop_reason == "runtime_or_controller_error"
    assert runtime.closed is True
    assert model.closed is True
    events = [json.loads(line) for line in (Path(result.run_dir) / "events.jsonl").read_text().splitlines()]
    assert any(event["event_type"] == "run_failed" for event in events)
    assert any(event["event_type"] == "run_finished" and event["status"] == "error" for event in events)
    report = generate_basic_report(Path(result.run_dir)).read_text(encoding="utf-8")
    assert "runtime_or_controller_error" in report


def test_model_initialization_failure_has_a_durable_run_record(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)

    def broken_model():
        raise RuntimeError("synthetic provider startup failure")

    service = make_service(broken_model, lambda *_: pytest.fail("runtime must not start"))
    result = service.run(workspace, IMAGE, tmp_path / "runs", RunLimits(max_turns=1))

    state_path = Path(result.run_dir) / "run-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    events = [json.loads(line) for line in (Path(result.run_dir) / "events.jsonl").read_text().splitlines()]
    failure = next(event for event in events if event["event_type"] == "run_failed")
    report = generate_basic_report(Path(result.run_dir)).read_text(encoding="utf-8")

    assert result.status == "provider_error"
    assert result.stop_reason == "provider_initialization_error"
    assert state["state"] == "failed"
    assert state["result_status"] == "provider_error"
    assert state["recovery"]["automatic_retry"] is False
    assert state["recovery"]["resume_supported"] is False
    assert failure["phase"] == "provider_initialization"
    assert all(event["run_id"] == result.run_id for event in events)
    assert "provider_initialization_error" in report
    assert "start a new run" in report
    assert state_path.stat().st_mode & 0o777 == 0o600


def test_provider_timeout_is_a_terminal_timed_out_lifecycle(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)

    class TimeoutModel:
        closed = False

        def run_turn(self, prompt, tools, on_tool_call, *, timeout):
            del prompt, tools, on_tool_call, timeout
            raise TimeoutError("synthetic provider timeout")

        def close(self) -> None:
            self.closed = True

    model = TimeoutModel()
    service = make_service(lambda: model, lambda root, image: QuietRuntime(root, image))
    result = service.run(workspace, IMAGE, tmp_path / "runs", RunLimits(max_turns=1))

    state = json.loads((Path(result.run_dir) / "run-state.json").read_text(encoding="utf-8"))
    events = [json.loads(line) for line in (Path(result.run_dir) / "events.jsonl").read_text().splitlines()]
    assert result.status == "provider_error"
    assert result.stop_reason == "provider_timeout"
    assert state["state"] == "timed_out"
    assert state["result_status"] == "provider_error"
    assert model.closed is True
    assert any(event["event_type"] == "provider_error" and event["kind"] == "timeout" for event in events)


def test_provider_cleanup_error_is_visible_in_run_state(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)

    class CloseFailureModel(FakeModelSession):
        def close(self) -> None:
            raise RuntimeError("synthetic provider cleanup failure")

    service = make_service(lambda: CloseFailureModel(()), lambda root, image: QuietRuntime(root, image))
    result = service.run(workspace, IMAGE, tmp_path / "runs", RunLimits(max_turns=1))

    state = json.loads((Path(result.run_dir) / "run-state.json").read_text(encoding="utf-8"))
    events = [json.loads(line) for line in (Path(result.run_dir) / "events.jsonl").read_text().splitlines()]
    assert result.status == "unverified"
    assert result.cleanup_errors == ("RuntimeError",)
    assert state["state"] == "completed"
    assert state["cleanup_status"] == "error"
    assert any(event["event_type"] == "provider_close_error" for event in events)


def test_partial_run_can_be_reported_as_incomplete_and_restarted(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    started = threading.Event()
    release = threading.Event()

    class BlockingModel:
        closed = False

        def run_turn(self, prompt, tools, on_tool_call, *, timeout):
            del prompt, tools, on_tool_call, timeout
            started.set()
            assert release.wait(5)
            raise RuntimeError("synthetic interrupted turn")

        def cancel(self) -> None:
            release.set()

        def close(self) -> None:
            self.closed = True

    model = BlockingModel()
    service = make_service(lambda: model, lambda root, image: QuietRuntime(root, image))
    control = RunControl()
    thread, outcome = run_in_thread(service, workspace, tmp_path, control)
    assert started.wait(3)
    run_dirs = list((tmp_path / "runs").iterdir())
    assert len(run_dirs) == 1

    report = generate_basic_report(run_dirs[0]).read_text(encoding="utf-8")
    state = json.loads((run_dirs[0] / "run-state.json").read_text(encoding="utf-8"))
    assert state["state"] == "running"
    assert "incomplete (running)" in report
    assert "Resume: `unsupported; review partial evidence and start a new run`" in report

    assert control.cancel() is True
    thread.join(5)
    assert not thread.is_alive()
    assert "error" not in outcome
    assert outcome["result"].status == "user_cancelled"
    assert model.closed is True


def test_report_marks_and_skips_an_incomplete_trailing_event(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    model = FakeModelSession(())
    service = make_service(lambda: model, lambda root, image: QuietRuntime(root, image))
    result = service.run(workspace, IMAGE, tmp_path / "runs", RunLimits(max_turns=1))
    with (Path(result.run_dir) / "events.jsonl").open("ab") as stream:
        stream.write(b'{"seq":')

    report = generate_basic_report(Path(result.run_dir)).read_text(encoding="utf-8")

    assert "incomplete trailing event; that fragment was omitted" in report
    assert "run_state_changed" in report


def test_interactive_session_round_trip_persists_transcript_and_closes(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)

    class FakeInteractiveRuntime(QuietRuntime):
        def __init__(self, challenge_root: Path, image: str) -> None:
            super().__init__(challenge_root, image)
            self.session_state: dict[str, dict] = {}

        def start_interactive(self, session_id, argv, *, on_output, on_closed) -> None:
            assert argv == ["python3", "-q"]
            self.session_state[session_id] = {
                "pending": bytearray(), "on_output": on_output, "on_closed": on_closed,
            }
            on_output(b"ready> ")

        def send_interactive(self, session_id: str, data: bytes) -> None:
            state = self.session_state[session_id]
            state["on_output"](b"echo:" + data)
            state["pending"].extend(b"ready> echo:" + data)

        def read_interactive(self, session_id: str, *, timeout: float, maximum_bytes: int):
            del timeout
            state = self.session_state[session_id]
            data = bytes(state["pending"][:maximum_bytes])
            del state["pending"][:maximum_bytes]
            return InteractiveReadResult(data, "running", None)

        def close_interactive(self, session_id: str):
            state = self.session_state[session_id]
            state["closed"] = True
            state["on_closed"]("closed", 0)
            return InteractiveReadResult(b"", "closed", 0)

    class InteractiveModel:
        closed = False
        session_id: str | None = None
        observed_output = ""

        def run_turn(self, prompt, tools, on_tool_call, *, timeout):
            del prompt, timeout
            tool_names = {tool.name for tool in tools}
            assert {"session_start", "session_send", "session_read", "session_close"} <= tool_names
            if self.session_id is None:
                reply = on_tool_call(ToolCall("session_start", {"argv": ["python3", "-q"]}))
                self.session_id = json.loads(reply.content)["session_id"]
                return TurnResult(text="", tool_calls=1)
            session_id = self.session_id
            on_tool_call(ToolCall("session_send", {"session_id": session_id, "input": "answer\n"}))
            read = on_tool_call(ToolCall("session_read", {"session_id": session_id, "wait_seconds": 0}))
            self.observed_output = json.loads(read.content)["output"]
            on_tool_call(ToolCall("session_close", {"session_id": session_id}))
            on_tool_call(ToolCall("candidate_submit", {"candidate": CANDIDATE}))
            on_tool_call(ToolCall("run_complete", {"outcome": "candidate_unverified", "candidate_id": "candidate-1", "summary": "Retain candidate", "unresolved": []}))
            return TurnResult(text="", tool_calls=4)

        def close(self) -> None:
            self.closed = True

    model = InteractiveModel()
    runtime = FakeInteractiveRuntime(workspace / "input", IMAGE)
    service = make_service(lambda: model, lambda *_: runtime)
    result = service.run(
        workspace, IMAGE, tmp_path / "runs",
        RunLimits(max_turns=3, max_tool_calls=8),
    )

    assert result.status == "candidate_unverified"
    assert "ready> echo:answer" in model.observed_output
    assert model.closed is True
    assert runtime.closed is True
    assert runtime.session_state[model.session_id]["closed"] is True
    transcript_path = Path(result.run_dir) / "sessions" / f"{model.session_id}.jsonl"
    transcript = [json.loads(line) for line in transcript_path.read_text(encoding="utf-8").splitlines()]
    assert [record["direction"] for record in transcript] == ["state", "output", "state", "input", "output", "state"]
    assert transcript[-1]["state"] == "closed"
    events = [json.loads(line) for line in (Path(result.run_dir) / "events.jsonl").read_text().splitlines()]
    transcript_events = [event for event in events if event["event_type"] == "session_transcript"]
    assert transcript_events
    assert all(event["session_id"] == model.session_id for event in transcript_events)
    assert all(event["run_id"] == result.run_id for event in transcript_events)
    report = generate_basic_report(Path(result.run_dir)).read_text(encoding="utf-8")
    assert transcript_path.relative_to(Path(result.run_dir)).as_posix() in report
    assert "echo:answer" not in report
    assert CANDIDATE not in report


def test_interactive_session_idle_timeout_terminates_the_process() -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; print('ready', flush=True); time.sleep(30)"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
    )
    forced = threading.Event()
    closed: list[tuple[str, int | None]] = []
    session = InteractiveSession(
        str(uuid.uuid4()),
        process,
        idle_timeout=0.2,
        total_timeout=2,
        output_limit=1024,
        on_output=lambda _data: None,
        on_closed=lambda status, exit_code: closed.append((status, exit_code)),
        on_force_close=forced.set,
    )

    first_read = session.read(timeout=1, maximum_bytes=128)
    timed_out_read = session.read(timeout=1, maximum_bytes=128)
    session.close()

    assert b"ready" in first_read.data
    assert timed_out_read.status == "idle_timeout"
    assert timed_out_read.timed_out is True
    assert forced.is_set()
    assert process.poll() is not None
    assert closed == [("idle_timeout", process.returncode)]


def test_docker_runtime_uses_tty_exec_for_interactive_session_without_starting_daemon(tmp_path: Path) -> None:
    process_calls: list[tuple[list[str], dict[str, object]]] = []
    received_output: list[bytes] = []
    closed: list[tuple[str, int | None]] = []
    tty_descriptors: list[bool] = []

    class FinishedProcess:
        def __init__(self) -> None:
            self.stdin = None
            self.stdout = None
            self.returncode = 0

        def poll(self) -> int:
            return 0

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return 0

        def kill(self) -> None:
            self.returncode = -9

    runtime = DockerRuntime(tmp_path, IMAGE)
    runtime._started = True

    def fake_popen(command: list[str], **kwargs: object) -> FinishedProcess:
        process_calls.append((command, kwargs))
        stdin_fd = kwargs["stdin"]
        stdout_fd = kwargs["stdout"]
        assert isinstance(stdin_fd, int)
        assert isinstance(stdout_fd, int)
        tty_descriptors.append(os.isatty(stdin_fd) and os.isatty(stdout_fd))
        os.write(stdout_fd, b"ready> ")
        return FinishedProcess()

    session_id = str(uuid.uuid4())
    with patch("ctfbot.runtime.docker.subprocess.Popen", side_effect=fake_popen), patch(
        "ctfbot.runtime.docker.subprocess.run", return_value=subprocess.CompletedProcess([], 0)
    ):
        runtime.start_interactive(
            session_id,
            ["python3", "-q"],
            on_output=received_output.append,
            on_closed=lambda status, code: closed.append((status, code)),
        )
        result = runtime.read_interactive(session_id, timeout=1, maximum_bytes=128)
        runtime.close_interactive(session_id)
        runtime.close()

    command, options = process_calls[0]
    assert command[1:7] == ["exec", "--interactive", "--tty", "--workdir", "/work", runtime.container_name]
    assert command[-2:] == ["python3", "-q"]
    assert "--privileged" not in command
    assert options["stdin"] == options["stdout"]
    assert tty_descriptors == [True]
    assert result.data == b"ready> "
    assert received_output == [b"ready> "]
    assert closed == [("exited", 0)]


def test_docker_runtime_pty_supports_a_real_interactive_child(tmp_path: Path) -> None:
    real_popen = subprocess.Popen
    runtime = DockerRuntime(tmp_path, IMAGE)
    runtime._started = True

    def local_child(_command: list[str], **kwargs: object) -> subprocess.Popen[bytes]:
        script = (
            "import sys; print('input_is_tty=' + str(sys.stdin.isatty()), flush=True); "
            "line = sys.stdin.readline(); print('received=' + line.strip(), flush=True)"
        )
        return real_popen([sys.executable, "-u", "-c", script], **kwargs)

    session_id = str(uuid.uuid4())
    with patch("ctfbot.runtime.docker.subprocess.Popen", side_effect=local_child):
        runtime.start_interactive(
            session_id,
            ["python3", "-q"],
            on_output=lambda _data: None,
            on_closed=lambda _status, _code: None,
        )
        first = runtime.read_interactive(session_id, timeout=1, maximum_bytes=256)
        runtime.send_interactive(session_id, b"hello\n")
        chunks = [first.data]
        deadline = time.monotonic() + 2
        while b"received=hello" not in b"".join(chunks) and time.monotonic() < deadline:
            chunks.append(runtime.read_interactive(session_id, timeout=0.2, maximum_bytes=256).data)
        final = runtime.close_interactive(session_id)

    output = b"".join(chunks) + final.data
    assert b"input_is_tty=True" in output
    assert b"hello" in output
    assert b"received=hello" in output


def test_stop_during_interactive_read_closes_session_and_keeps_transcript(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    read_started = threading.Event()
    release_read = threading.Event()

    class BlockingInteractiveRuntime(QuietRuntime):
        def __init__(self, challenge_root: Path, image: str) -> None:
            super().__init__(challenge_root, image)
            self.session_id: str | None = None
            self.on_closed = None
            self.session_closed = False

        def start_interactive(self, session_id, argv, *, on_output, on_closed) -> None:
            del argv
            self.session_id = session_id
            self.on_closed = on_closed
            on_output(b"ready> ")

        def send_interactive(self, session_id: str, data: bytes) -> None:
            del session_id, data

        def read_interactive(self, session_id: str, *, timeout: float, maximum_bytes: int):
            del session_id, timeout, maximum_bytes
            read_started.set()
            assert release_read.wait(5)
            return InteractiveReadResult(b"", "cancelled", None)

        def close_interactive(self, session_id: str):
            assert session_id == self.session_id
            if not self.session_closed:
                self.session_closed = True
                self.on_closed("closed", 0)
            return InteractiveReadResult(b"", "closed", 0)

        def cancel(self) -> None:
            release_read.set()
            if not self.session_closed:
                self.session_closed = True
                self.on_closed("cancelled", None)

    class InteractiveModel:
        session_id: str | None = None
        closed = False

        def run_turn(self, prompt, tools, on_tool_call, *, timeout):
            del prompt, tools, timeout
            if self.session_id is None:
                reply = on_tool_call(ToolCall("session_start", {"argv": ["python3", "-q"]}))
                self.session_id = json.loads(reply.content)["session_id"]
                return TurnResult(text="", tool_calls=1)
            on_tool_call(ToolCall("session_read", {"session_id": self.session_id, "wait_seconds": 10}))
            return TurnResult(text="", tool_calls=1)

        def close(self) -> None:
            self.closed = True

    model = InteractiveModel()
    runtime = BlockingInteractiveRuntime(workspace / "input", IMAGE)
    service = make_service(lambda: model, lambda *_: runtime)
    control = RunControl()
    outcome: dict[str, object] = {}

    def run() -> None:
        try:
            outcome["result"] = service.run(
                workspace, IMAGE, tmp_path / "runs",
                RunLimits(max_turns=2, max_tool_calls=4),
                control=control,
            )
        except BaseException as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert read_started.wait(3)

    assert control.cancel() is True
    thread.join(5)

    assert not thread.is_alive()
    assert "error" not in outcome
    result = outcome["result"]
    assert result.status == "user_cancelled"
    assert runtime.session_closed is True
    assert runtime.closed is True
    assert model.closed is True
    transcript_path = Path(result.run_dir) / "sessions" / f"{model.session_id}.jsonl"
    transcript = [json.loads(line) for line in transcript_path.read_text(encoding="utf-8").splitlines()]
    assert transcript[-1]["state"] == "cancelled"


def test_teardown_failure_after_completion_keeps_one_terminal_result(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    control = RunControl()
    model = FakeModelSession((ScriptedTurn(tool_calls=(ToolCall("candidate_submit", {"candidate": CANDIDATE}), ToolCall("run_complete", {"outcome": "candidate_unverified", "candidate_id": "candidate-1", "summary": "Retain candidate", "unresolved": []}),)),))

    class TeardownFailureRuntime(QuietRuntime):
        def __exit__(self, *_: object) -> None:
            control.cancel()
            raise RuntimeError("synthetic teardown failure")

        def cancel(self) -> None:
            pass

    runtime = TeardownFailureRuntime(workspace / "input", IMAGE)
    service = make_service(lambda: model, lambda *_: runtime)

    result = service.run(
        workspace, IMAGE, tmp_path / "runs", RunLimits(max_turns=1), control=control
    )

    events = [json.loads(line) for line in (Path(result.run_dir) / "events.jsonl").read_text().splitlines()]
    terminal_events = [event for event in events if event["event_type"] == "run_finished"]
    state = json.loads((Path(result.run_dir) / "run-state.json").read_text(encoding="utf-8"))
    assert result.status == "candidate_unverified"
    assert len(terminal_events) == 1
    assert terminal_events[0]["status"] == "candidate_unverified"
    assert any(event["event_type"] == "run_cleanup_error" for event in events)
    assert state["state"] == "completed"
    assert state["cleanup_status"] == "recovered"


def test_codex_app_server_interrupt_terminates_owned_process_only() -> None:
    server = CodexAppServer.__new__(CodexAppServer)
    process = Mock()
    process.poll.return_value = None
    server._process = process
    server._messages = queue.Queue()

    server.interrupt()

    process.terminate.assert_called_once_with()
    process.kill.assert_not_called()
    process.wait.assert_called_once()


def test_codex_app_server_interrupt_kills_and_reaps_process_that_ignores_terminate() -> None:
    server = CodexAppServer.__new__(CodexAppServer)
    process = Mock()
    process.poll.return_value = None
    process.wait.side_effect = [subprocess.TimeoutExpired("codex", timeout=0.25), 0]
    server._process = process
    server._messages = queue.Queue()

    server.interrupt()

    process.terminate.assert_called_once_with()
    process.kill.assert_called_once_with()
    assert process.wait.call_count == 2
    assert server._messages.get_nowait() is None


def test_docker_runtime_cancel_kills_active_exec_and_container(tmp_path: Path) -> None:
    runtime = DockerRuntime(tmp_path, IMAGE)
    runtime._started = True
    process = Mock()
    runtime._active_process = process
    with patch("ctfbot.runtime.docker.subprocess.run") as docker_run:
        runtime.cancel()

    process.kill.assert_called_once_with()
    docker_run.assert_called_once()
    assert docker_run.call_args.args[0][:2] == ["docker", "kill"]
