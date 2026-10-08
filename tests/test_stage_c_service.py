from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from ctfbot.agent.loop import RunLimits
from ctfbot.application.baseline import BaselineAdmissionError, run_baseline, validate_baseline_snapshot
from ctfbot.application.control import RunControl
from ctfbot.application.service import LocalChallengeService
from ctfbot.challenge.local_service import LocalServiceSpec
from ctfbot.challenge.service_fixture import SYNTHETIC_FLAG, import_service_fixture
from ctfbot.model_adapters.protocol import FakeModelSession, ToolCall, TurnResult
from ctfbot.reporting import generate_basic_report
from ctfbot.runtime.docker import CommandResult, DockerRuntime, RuntimeErrorSafe
from ctfbot.runtime.local_service import DockerLocalServiceRuntime


IMAGE = "sha256:" + "c" * 64


class FakeDocker:
    """Track Docker resources, labels and removal; no daemon or networking involved."""

    def __init__(self, *, service_running: bool = True, fail_remove: bool = False,
                 internal: bool = True, volumes: bool = False) -> None:
        self.resources: dict[str, dict] = {}
        self.calls: list[list[str]] = []
        self.service_running = service_running
        self.fail_remove = fail_remove
        self.internal = internal
        self.volumes = volumes
        self.real_run = subprocess.run
        self.real_popen = subprocess.Popen

    def popen(self, command, **kwargs):
        if command[0] != "docker":
            return self.real_popen(command, **kwargs)
        assert command[1] == "logs"
        return self.real_popen([sys.executable, "-c", "print('synthetic service log')"], **kwargs)

    def __call__(self, command, **kwargs):
        if command[0] != "docker":
            return self.real_run(command, **kwargs)
        del kwargs
        self.calls.append(command)
        args = command[1:]
        stdout = b""
        if args[:2] == ["image", "inspect"]:
            stdout = b'{"/data": {}}' if self.volumes else b"null"
        elif args[:2] == ["network", "create"]:
            label = args[args.index("--label") + 1].split("=", 1)
            options = dict(args[index + 1].split("=", 1) for index, value in enumerate(args) if value == "--opt")
            self.resources[args[-1]] = {"Labels": dict([label]), "Internal": self.internal, "Driver": "bridge",
                                       "EnableIPv6": False, "Options": options}
        elif args[0] == "create":
            label = args[args.index("--label") + 1].split("=", 1)
            self.resources[args[args.index("--name") + 1]] = {"Labels": dict([label])}
        elif args[0] == "inspect":
            stdout = b"true" if self.service_running else b"false"
        elif len(args) > 1 and args[1] == "inspect":
            record = self.resources.get(args[-1])
            if record is None:
                return subprocess.CompletedProcess(command, 1, b"", f"No such object: {args[-1]}".encode())
            stdout = json.dumps(record["Labels"] if "--format" in args else [record]).encode()
        elif args[0] == "rm" or (len(args) > 1 and args[1] == "rm"):
            if self.fail_remove:
                return subprocess.CompletedProcess(command, 1, b"", b"synthetic removal failure")
            self.resources.pop(args[-1], None)
        return subprocess.CompletedProcess(command, 0, stdout, b"")


def fixture(root: Path) -> tuple[Path, Path]:
    return import_service_fixture(root / "fixture", IMAGE, authorize_model_data=True)


def events(result) -> list[dict]:
    return [json.loads(line) for line in (Path(result.run_dir) / "events.jsonl").read_text().splitlines()]


def test_service_fixture_import_is_private_readonly_and_defaults_to_no_model_authorization(tmp_path: Path) -> None:
    workspace = import_service_fixture(tmp_path / "fixture", IMAGE)
    _, provenance, task = validate_baseline_snapshot(workspace)
    assert provenance["model_data_authorized"] is False
    assert SYNTHETIC_FLAG not in task
    assert not (workspace.parent / "oracle.json").exists()
    assert (workspace.stat().st_mode & 0o777) == 0o700
    assert ((workspace / "provenance.json").stat().st_mode & 0o777) == 0o444
    with pytest.raises(FileExistsError):
        import_service_fixture(tmp_path / "fixture", IMAGE)


@pytest.mark.parametrize("changes", [
    {"image": "example/service:latest"}, {"port": 80}, {"port": True},
    {"schema_version": True}, {"startup_timeout_seconds": 0},
    {"runtime_authorized": False}, {"authorization_basis": " "},
    {"privileged": True}, {"network": "host"}, {"mounts": ["/var/run/docker.sock"]},
    {"ports": ["0.0.0.0:31337:31337"]}, {"argv": ["relative-program"]},
])
def test_unsafe_service_manifest_is_rejected_before_dependencies(tmp_path: Path, changes: dict) -> None:
    workspace = fixture(tmp_path)
    path = workspace / "provenance.json"
    provenance = json.loads(path.read_text())
    provenance["local_service"].update(changes)
    path.chmod(0o600)
    path.write_text(json.dumps(provenance))
    path.chmod(0o444)
    with pytest.raises(BaselineAdmissionError, match="invalid local service"):
        validate_baseline_snapshot(workspace)


def test_default_application_previews_but_does_not_enable_live_services(tmp_path: Path) -> None:
    workspace = fixture(tmp_path)
    service = LocalChallengeService(model_factory=lambda: pytest.fail("model started"), model_metadata={})
    preview = service.preview(workspace, IMAGE, tmp_path / "runs")
    assert preview.model_data_authorized
    assert preview.service_endpoint == "challenge:31337"
    assert not preview.start_allowed
    assert "isolation acceptance" in preview.start_block_reason
    with pytest.raises(BaselineAdmissionError, match="isolation acceptance"):
        service.run(workspace, IMAGE, tmp_path / "runs")
    with pytest.raises(BaselineAdmissionError, match="isolation acceptance"):
        run_baseline(workspace=workspace, runtime_image=IMAGE,
                     runs_root=tmp_path / "runs", model_factory=lambda: pytest.fail("model started"),
                     model_metadata={})
    assert not (tmp_path / "runs").exists()


def test_local_service_health_candidate_report_and_cleanup(tmp_path: Path) -> None:
    workspace = fixture(tmp_path)
    docker = FakeDocker()
    states: list[str] = []
    runtimes: list[DockerLocalServiceRuntime] = []

    def factory(root, image, spec, sink):
        def record(details):
            states.append(details["state"])
            sink(details)
        runtime = DockerLocalServiceRuntime(root, image, spec, event_sink=record)
        runtimes.append(runtime)
        return runtime

    def probe_or_solve(runtime, argv, *, timeout):
        del timeout
        assert runtime._started
        if "socket.create_connection" in " ".join(argv):
            return CommandResult(0, b"", b"")
        return CommandResult(0, (SYNTHETIC_FLAG + "\n").encode(), b"")

    class Model:
        def run_turn(self, prompt, tools, on_tool_call, *, timeout):
            del timeout
            assert "challenge:31337" in prompt
            assert "network-disabled" not in prompt
            assert "challenge:31337" in next(spec.description for spec in tools if spec.name == "command_run")
            reply = on_tool_call(ToolCall("command_run", {"argv": ["python3", "solve.py"]}))
            assert SYNTHETIC_FLAG in reply.content
            on_tool_call(ToolCall("candidate_submit", {"candidate": SYNTHETIC_FLAG}))
            on_tool_call(ToolCall("run_complete", {"outcome": "candidate_unverified", "candidate_id": "candidate-1", "summary": "Synthetic response recorded", "unresolved": []}))
            return TurnResult("", tool_calls=2)

        def close(self):
            pass

    def make_model():
        assert states[-1] == "healthy"
        return Model()

    service = LocalChallengeService(model_factory=make_model, model_metadata={"provider": "synthetic"},
                                    service_runtime_factory=factory)
    with patch("ctfbot.runtime.local_service.subprocess.run", side_effect=docker), patch(
        "ctfbot.runtime.local_service.subprocess.Popen", side_effect=docker.popen
    ), patch.object(
        DockerRuntime, "execute", probe_or_solve
    ):
        result = service.run(workspace, IMAGE, tmp_path / "runs", RunLimits(max_turns=1))

    assert result.status == "candidate_unverified"
    assert not docker.resources
    assert states == ["preparing", "network_created", "service_started", "healthy", "logs_collected", "cleaned"]
    recorded = events(result)
    healthy = next(e["seq"] for e in recorded if e.get("state") == "healthy")
    provider = next(e["seq"] for e in recorded if e["event_type"] == "provider_initialized")
    assert healthy < provider
    log = next(e["log_evidence"] for e in recorded if e.get("state") == "logs_collected")
    assert (Path(result.run_dir) / log["artifact"]).read_bytes() == b"synthetic service log\n"
    report = generate_basic_report(Path(result.run_dir)).read_text()
    assert "challenge:31337" in report and "healthy" in report and "cleaned" in report
    assert SYNTHETIC_FLAG not in report
    creates = [call for call in docker.calls if call[1] == "create"]
    assert len(creates) == 2
    for command in creates:
        assert f"--network={runtimes[0].network_name}" in command
        assert "--read-only" in command and "--cap-drop=ALL" in command
        assert not any(flag in command for flag in ["--privileged", "--publish", "-p", "--network=host"])
        mounts = [arg for arg in command if arg.startswith("type=bind,")]
        assert all(f"source={workspace / 'input'},target=/challenge,readonly" in mount for mount in mounts)


@pytest.mark.parametrize("outcome", ["startup_failure", "cancel_during_readiness", "cancel_during_agent",
                                     "provider_failure", "cleanup_failure"])
def test_local_service_failure_and_cancel_keep_evidence_and_cleanup(tmp_path: Path, outcome: str) -> None:
    workspace = fixture(tmp_path)
    docker = FakeDocker(service_running=outcome != "startup_failure", fail_remove=outcome == "cleanup_failure")
    control = RunControl()
    provider_calls = []

    def factory(root, image, spec, sink):
        return DockerLocalServiceRuntime(root, image, spec, event_sink=sink)

    def probe(runtime, argv, *, timeout):
        del argv, timeout
        if outcome == "cancel_during_readiness":
            control.cancel()
        return CommandResult(0, b"", b"")

    def make_model():
        provider_calls.append(True)
        if outcome == "provider_failure":
            raise ValueError("synthetic provider failure")
        if outcome == "cancel_during_agent":
            class CancellingModel:
                def run_turn(self, *_args, **_kwargs):
                    control.cancel()
                    return TurnResult("")

                def close(self):
                    pass
            return CancellingModel()
        return FakeModelSession(())

    service = LocalChallengeService(model_factory=make_model, model_metadata={}, service_runtime_factory=factory)
    with patch("ctfbot.runtime.local_service.subprocess.run", side_effect=docker), patch(
        "ctfbot.runtime.local_service.subprocess.Popen", side_effect=docker.popen
    ), patch.object(
        DockerRuntime, "execute", probe
    ):
        result = service.run(workspace, IMAGE, tmp_path / "runs", RunLimits(max_turns=1), control=control)
    recorded = events(result)
    states = [e["state"] for e in recorded if e["event_type"] == "service_lifecycle"]
    if outcome == "cleanup_failure":
        assert docker.resources
        assert "cleanup_failed" in states
        state = json.loads((Path(result.run_dir) / "run-state.json").read_text())
        assert state["cleanup_status"] == "error"
    else:
        assert not docker.resources
        assert "cleaned" in states
    if outcome in {"startup_failure", "cancel_during_readiness"}:
        assert not provider_calls
    assert result.status == {"startup_failure": "error", "cancel_during_readiness": "user_cancelled",
                             "cancel_during_agent": "user_cancelled",
                             "provider_failure": "provider_error", "cleanup_failure": "unverified"}[outcome]
    assert "service_lifecycle" in generate_basic_report(Path(result.run_dir)).read_text()


@pytest.mark.parametrize("kwargs", [{"internal": False}, {"volumes": True}])
def test_runtime_rejects_unsafe_image_or_network_without_starting_provider(tmp_path: Path, kwargs: dict) -> None:
    workspace = fixture(tmp_path)
    docker = FakeDocker(**kwargs)
    service = LocalChallengeService(
        model_factory=lambda: pytest.fail("model started"), model_metadata={},
        service_runtime_factory=lambda root, image, spec, sink: DockerLocalServiceRuntime(
            root, image, spec, event_sink=sink),
    )
    with patch("ctfbot.runtime.local_service.subprocess.run", side_effect=docker):
        result = service.run(workspace, IMAGE, tmp_path / "runs")
    assert result.status == "error"
    assert not docker.resources
    assert not any(call[1] == "create" for call in docker.calls)


def test_cleanup_refuses_resources_owned_by_another_run(tmp_path: Path) -> None:
    workspace = fixture(tmp_path)
    spec = LocalServiceSpec.from_manifest(json.loads((workspace / "provenance.json").read_text())["local_service"])
    runtime = DockerLocalServiceRuntime(workspace / "input", IMAGE, spec, event_sink=lambda _details: None)
    runtime._service_attempted = True
    docker = FakeDocker()
    docker.resources[runtime.service_name] = {"Labels": {"ctfbot.owner": "another-run"}}
    with patch("ctfbot.runtime.local_service.subprocess.run", side_effect=docker):
        with pytest.raises(RuntimeErrorSafe, match="cleanup failed"):
            runtime.close()
    assert runtime.service_name in docker.resources
    assert not any("rm" in call for call in docker.calls)


@pytest.mark.parametrize("wall_limit", [None, 0.2])
def test_readiness_timeout_cleans_service_without_starting_provider(tmp_path: Path, wall_limit: float | None) -> None:
    workspace = fixture(tmp_path)
    path = workspace / "provenance.json"
    provenance = json.loads(path.read_text())
    provenance["local_service"]["startup_timeout_seconds"] = 1
    path.chmod(0o600)
    path.write_text(json.dumps(provenance))
    path.chmod(0o444)
    docker = FakeDocker()
    service = LocalChallengeService(
        model_factory=lambda: pytest.fail("model started"), model_metadata={},
        service_runtime_factory=lambda root, image, spec, sink: DockerLocalServiceRuntime(
            root, image, spec, event_sink=sink),
    )
    with patch("ctfbot.runtime.local_service.subprocess.run", side_effect=docker), patch(
        "ctfbot.runtime.local_service.subprocess.Popen", side_effect=docker.popen
    ), patch.object(DockerRuntime, "execute", return_value=CommandResult(1, b"", b"not ready")):
        result = service.run(workspace, IMAGE, tmp_path / "runs",
                             RunLimits(wall_time_seconds=wall_limit or 1800))
    assert result.status == ("budget_exhausted" if wall_limit else "error")
    assert not docker.resources
    assert not any(e.get("state") == "healthy" for e in events(result))
    failed = next(e for e in events(result) if e["event_type"] == "run_failed")
    assert failed["failure_class"] == ("timeout" if wall_limit else "environment_error")


def test_offline_import_cannot_silently_activate_service_config(tmp_path: Path) -> None:
    workspace = fixture(tmp_path)
    path = workspace / "provenance.json"
    provenance = json.loads(path.read_text())
    provenance["import_mode"] = "static_files_only"
    path.chmod(0o600)
    path.write_text(json.dumps(provenance))
    path.chmod(0o444)
    with pytest.raises(BaselineAdmissionError, match="requires local_service import mode"):
        validate_baseline_snapshot(workspace)


def test_missing_daemon_is_not_mistaken_for_a_removed_resource(tmp_path: Path) -> None:
    workspace = fixture(tmp_path)
    spec = LocalServiceSpec.from_manifest(json.loads((workspace / "provenance.json").read_text())["local_service"])
    states = []
    runtime = DockerLocalServiceRuntime(workspace / "input", IMAGE, spec, event_sink=states.append)
    runtime._network_attempted = True
    missing_socket = subprocess.CompletedProcess([], 1, b"", b"dial unix /var/run/docker.sock: no such file or directory")
    with patch("ctfbot.runtime.local_service.subprocess.run", return_value=missing_socket):
        with pytest.raises(RuntimeErrorSafe, match="cleanup failed"):
            runtime.close()
    assert runtime._network_attempted
    assert states[-1]["state"] == "cleanup_failed"


def test_evidence_write_failure_still_removes_created_resources(tmp_path: Path) -> None:
    workspace = fixture(tmp_path)
    spec = LocalServiceSpec.from_manifest(json.loads((workspace / "provenance.json").read_text())["local_service"])
    docker = FakeDocker()

    def failing_sink(details):
        if details["state"] not in {"preparing", "network_created", "service_started"}:
            raise OSError("synthetic disk full")

    runtime = DockerLocalServiceRuntime(workspace / "input", IMAGE, spec, event_sink=failing_sink)
    with patch("ctfbot.runtime.local_service.subprocess.run", side_effect=docker), patch(
        "ctfbot.runtime.local_service.subprocess.Popen", side_effect=docker.popen
    ), patch.object(DockerRuntime, "execute", return_value=CommandResult(0, b"", b"")):
        with pytest.raises(OSError, match="disk full"):
            runtime.start()
    assert not docker.resources
