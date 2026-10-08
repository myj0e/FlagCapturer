from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from ctfbot.agent.loop import RunLimits
from ctfbot.application.baseline import BaselineAdmissionError
from ctfbot.application.service import LocalChallengeService
from ctfbot.model_adapters.protocol import FakeModelSession
from ctfbot.tools.registry import CommandResult


IMAGE = "sha256:" + "a" * 64


def make_workspace(root: Path, *, authorized: bool = True) -> Path:
    workspace = root / "workspace"
    input_root = workspace / "input"
    input_root.mkdir(parents=True, mode=0o700)
    os.chmod(workspace, 0o700)
    payload = b"synthetic attachment bytes"
    (input_root / "cipher.txt").write_bytes(payload)
    os.chmod(input_root / "cipher.txt", 0o444)
    os.chmod(input_root, 0o555)

    task = "Analyze this synthetic attachment.\n"
    (workspace / "TASK.md").write_text(task, encoding="utf-8")
    os.chmod(workspace / "TASK.md", 0o444)
    challenge_id = "stage-b-synthetic"
    metadata_hash = hashlib.sha256(b"synthetic metadata").hexdigest()
    provenance = {
        "challenge_id": challenge_id,
        "source_commit": "synthetic-commit",
        "challenge_metadata_sha256": metadata_hash,
        "category": "crypto",
        "formal_admission": "admitted",
        "import_mode": "static_files_only",
        "authorization_scope": "private local evaluation; do not redistribute challenge assets",
        "model_data_authorized": authorized,
        "model_data_authorization_basis": "synthetic test authorization" if authorized else None,
        "task_sha256": hashlib.sha256(task.encode("utf-8")).hexdigest(),
        "input_files": [{
            "path": "cipher.txt",
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }],
    }
    (workspace / "provenance.json").write_text(json.dumps(provenance), encoding="utf-8")
    os.chmod(workspace / "provenance.json", 0o444)

    return workspace


class FakeRuntime:
    def __init__(self, challenge_root: Path, image: str) -> None:
        self.challenge_root = challenge_root
        self.image = image
        self.closed = False

    def __enter__(self) -> FakeRuntime:
        return self

    def __exit__(self, *_: object) -> None:
        self.closed = True

    def execute(self, argv: list[str], *, timeout: float) -> CommandResult:
        del argv, timeout
        return CommandResult(0, b"", b"")


def service_for(model_factory, runtime_factory, *, model_metadata=None) -> LocalChallengeService:
    return LocalChallengeService(
        model_factory=model_factory,
        model_metadata=model_metadata or {"provider": "fake"},
        runtime_factory=runtime_factory,
    )


def test_preview_reports_hash_authorization_verifier_and_offline_profile(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    service = service_for(lambda: pytest.fail("preview constructed a model"),
                          lambda *_: pytest.fail("preview constructed a runtime"))

    preview = service.preview(workspace, IMAGE, tmp_path / "runs", RunLimits())

    assert preview.challenge_id == "stage-b-synthetic"
    assert preview.import_mode == "static_files_only"
    assert preview.inputs[0].path == "cipher.txt"
    assert preview.inputs[0].sha256 == hashlib.sha256(b"synthetic attachment bytes").hexdigest()
    assert preview.inputs[0].bytes == len(b"synthetic attachment bytes")
    assert preview.model_data_authorized is True
    assert preview.authorization_basis == "synthetic test authorization"
    assert preview.verifier == "model-selected candidate; correctness unverified"
    assert preview.runtime_profile == "offline Docker; read-only input; bounded tmpfs workdir"
    assert preview.runtime_image == IMAGE
    assert "CTFBOT_SYNTHETIC{secret-value}" not in repr(preview)


def test_unauthorized_run_stops_before_model_or_runtime_factory(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path, authorized=False)
    calls: list[str] = []
    service = service_for(
        lambda: calls.append("model"),
        lambda *_: calls.append("runtime"),
    )

    with pytest.raises(BaselineAdmissionError, match="transmission to a model"):
        service.run(workspace, IMAGE, tmp_path / "runs", RunLimits(max_turns=1))

    assert calls == []
    assert not (tmp_path / "runs").exists()


def test_invalid_image_stops_before_model_or_runtime_factory(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    calls: list[str] = []
    service = service_for(lambda: calls.append("model"), lambda *_: calls.append("runtime"))

    with pytest.raises(ValueError, match="pinned"):
        service.run(workspace, "registry.example/tool:latest", tmp_path / "runs", RunLimits())

    assert calls == []
    assert not (tmp_path / "runs").exists()


def test_service_run_uses_injected_model_and_runtime_after_preflight(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    calls: list[str] = []
    runtime_instances: list[FakeRuntime] = []

    def make_model() -> FakeModelSession:
        calls.append("model")
        return FakeModelSession(())

    def make_runtime(root: Path, image: str) -> FakeRuntime:
        calls.append("runtime")
        runtime = FakeRuntime(root, image)
        runtime_instances.append(runtime)
        return runtime

    service = service_for(make_model, make_runtime)
    result = service.run(workspace, IMAGE, tmp_path / "runs", RunLimits(max_turns=1))

    assert result.status == "unverified"
    assert calls == ["model", "runtime"]
    assert runtime_instances[0].challenge_root == workspace / "input"
    assert runtime_instances[0].image == IMAGE
    assert runtime_instances[0].closed is True


def test_invalid_admission_stops_before_model_or_runtime_factory(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    provenance_path = workspace / "provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance["formal_admission"] = "pending"
    provenance_path.chmod(0o644)
    provenance_path.write_text(json.dumps(provenance), encoding="utf-8")
    provenance_path.chmod(0o444)
    calls: list[str] = []
    service = service_for(lambda: calls.append("model"), lambda *_: calls.append("runtime"))

    with pytest.raises(BaselineAdmissionError, match="formal A4 admission"):
        service.run(workspace, IMAGE, tmp_path / "runs", RunLimits())

    assert calls == []
    assert not (tmp_path / "runs").exists()
