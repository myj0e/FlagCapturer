from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from ctfbot.agent.loop import RunLimits
from ctfbot.application.memory import MemoryView, propose, review, revoke
from ctfbot.application.service import LocalChallengeService
from ctfbot.benchmark.evaluation import _aggregate, audit_separation, load_dataset, run_evaluation
from ctfbot.challenge.domain_fixtures import create_domain_dataset
from ctfbot.evidence.store import EvidenceStore
from ctfbot.model_adapters.protocol import FakeModelSession, ScriptedTurn, ToolCall
from ctfbot.reporting.replay import audit_run, replay_run
from ctfbot.tools.registry import CommandResult, ToolRegistry

IMAGE = "sha256:" + "a" * 64


@pytest.mark.parametrize("failure", [{"environment_error": True}, {"status": "provider_error"}])
def test_verified_then_failure_keeps_eligible_denominator(failure):
    metrics = _aggregate([{"run_id": "authored", "verified": True, **failure}])
    assert metrics["verified_over_eligible_started"] == [1, 1]


class AuthoredHostRuntime:
    """Unit-only adapter for authored snippets/data, never arbitrary challenges."""
    def __init__(self, root, image):
        self.root = root
        self.work = root.parent.parent / "unit-work"
        self.work.mkdir(exist_ok=True)
    def __enter__(self): return self
    def __exit__(self, *args): self.close()
    def close(self): pass
    def execute(self, argv, *, timeout):
        translated = [re.sub(r"/challenge(?=/|['\"]|$)|/work(?=/|['\"]|$)",
                             lambda match: str(self.root if match.group() == "/challenge" else self.work), part) for part in argv]
        result = subprocess.run(translated, cwd=self.work, capture_output=True, timeout=timeout)
        return CommandResult(result.returncode, result.stdout, result.stderr)


@pytest.fixture
def dataset(tmp_path):
    if not (shutil.which("cc") or shutil.which("gcc")):
        pytest.skip("authored binary fixtures need a compiler")
    return create_domain_dataset(tmp_path / "dataset", authorize_model_data=True)


def registry(tmp_path, case):
    evidence = EvidenceStore(tmp_path / "evidence")
    work = tmp_path / "controller"
    work.mkdir()
    return ToolRegistry(case["workspace"] / "input", work, evidence,
                        AuthoredHostRuntime(case["workspace"] / "input", IMAGE), case["oracle"])


@pytest.mark.parametrize("pack,operation,path", [
    ("crypto", "decode", "cipher.b64"), ("forensics", "archive", "evidence.zip"),
    ("stego", "ppm_lsb", "carrier.ppm"), ("reverse", "elf", "program.elf"),
    ("pwn", "elf", "program.elf"), ("web", "response", "response.http"),
])
def test_each_pack_has_working_authored_analysis(dataset, tmp_path, pack, operation, path):
    cases = load_dataset(dataset)["cases"]
    case = next(item for item in cases if item["category"] == pack)
    tools = registry(tmp_path, case)
    assert tools.invoke(ToolCall("workflow_discover", {})).reply.success
    assert tools.invoke(ToolCall("workflow_read", {"pack": pack})).reply.success
    result = tools.invoke(ToolCall("workflow_run", {"pack": pack, "operation": operation, "path": path}))
    assert result.reply.success, result.reply.content
    parsed = json.loads(json.loads(result.reply.content)["stdout"])
    assert parsed["pack"] == pack
    assert len(parsed["input_sha256"]) == 64


def test_script_hash_path_and_export_lineage(dataset, tmp_path):
    case = load_dataset(dataset)["cases"][0]
    tools = registry(tmp_path, case)
    source = "from pathlib import Path\nPath('result.txt').write_text('authored')\nprint('done')"
    assert tools.invoke(ToolCall("script_save", {"path": "solve.py", "source": source})).reply.success
    assert tools.invoke(ToolCall("script_run", {"path": "solve.py", "argv": []})).reply.success
    exported = tools.invoke(ToolCall("artifact_export", {"path": "result.txt", "parents": ["cipher.b64"]}))
    assert exported.reply.success
    assert json.loads(exported.reply.content)["parents"][0]["input_path"] == "cipher.b64"
    tools.runtime.work.joinpath("solve.py").write_text("print('modified')")
    assert not tools.invoke(ToolCall("script_run", {"path": "solve.py", "argv": []})).reply.success
    assert tools.invoke(ToolCall("script_save", {"path": "../outside.py", "source": "pass"})).result["status"] == "policy_denied"


def test_memory_review_namespaces_source_changes_and_revocation(tmp_path):
    source = tmp_path / "note.md"
    source.write_text("Use a bounded hypothesis and retain evidence hashes.")
    root = tmp_path / "memory"
    identity = propose(root, source, namespace="manuals", source_url="repo:authored", license="MIT", tags=["crypto"])
    assert not MemoryView(root, namespaces=("manuals",)).entries
    version = review(root, identity, reviewer="unit-reviewer", basis="authored note checked", decision="approve", contamination="generic")
    frozen = MemoryView(root, namespaces=("manuals",))
    assert version in frozen.entries
    revoke(root, version, basis="unit revocation")
    assert not MemoryView(root, namespaces=("manuals",)).entries
    assert version in frozen.entries
    with pytest.raises(PermissionError): MemoryView(root, namespaces=("writeups",))
    source.write_text("FLAG{not_blind_safe}")
    contaminated = propose(root, source, namespace="manuals", source_url="repo:authored", license="MIT", tags=[])
    with pytest.raises(ValueError):
        review(root, contaminated, reviewer="unit", basis="check", decision="approve", contamination="generic")
    source.write_text("changed after proposal")
    with pytest.raises(ValueError):
        review(root, identity, reviewer="unit", basis="check", decision="approve", contamination="generic")


def test_dataset_evaluation_budget_metrics_and_replay(dataset, tmp_path):
    loaded = load_dataset(dataset)
    flags = [json.loads(case["oracle"].read_text())["flag"] for case in loaded["cases"]]
    queue = iter(flags)
    service = LocalChallengeService(model_factory=lambda: FakeModelSession([
        ScriptedTurn(tool_calls=(ToolCall("candidate_submit", {"candidate": next(queue)}),))]),
        model_metadata={"provider": "unit-only"}, runtime_factory=AuthoredHostRuntime)
    summary = run_evaluation(dataset_path=dataset, service=service, runtime_image=IMAGE, output=tmp_path / "evaluations",
                             limits=RunLimits(max_turns=1), max_total_turns=2, max_total_tool_calls=10)
    payload = json.loads(summary.read_text())
    assert payload["aggregate"]["verified_over_scheduled"] == [2, 6]
    assert payload["aggregate"]["not_run"] == 4
    assert payload["remaining_turns"] == 0
    assert payload["aggregate"]["cost_per_verified"] is None
    assert all(flag not in summary.read_text() for flag in flags)
    case = loaded["cases"][0]
    runtime_factory = lambda root, image: AuthoredHostRuntime(root, image)
    run_service = LocalChallengeService(model_factory=lambda: FakeModelSession([ScriptedTurn(tool_calls=(
        ToolCall("command_run", {"argv": ["python3", "-c", "print('stable authored output')"]}),))]),
        model_metadata={"provider": "unit-only"}, runtime_factory=runtime_factory)
    result = run_service.run(case["workspace"], case["oracle"], IMAGE, tmp_path / "original", RunLimits(max_turns=1))
    assert audit_run(Path(result.run_dir))["integrity"] == "passed"
    replay = replay_run(run_dir=Path(result.run_dir), workspace=case["workspace"], oracle=case["oracle"],
                        output=tmp_path / "replays", runtime_factory=runtime_factory)
    assert json.loads(replay.read_text())["status"] == "matched"
    artifact = next((Path(result.run_dir) / "artifacts").iterdir())
    artifact.write_bytes(b"modified")
    with pytest.raises(ValueError): audit_run(Path(result.run_dir))


def test_holdout_separation_and_unauthorized_preflight(dataset, tmp_path):
    holdout = create_domain_dataset(tmp_path / "holdout", split="holdout")
    assert audit_separation(dataset, holdout)["disjoint"]
    service = LocalChallengeService(model_factory=lambda: pytest.fail("unauthorized preflight opened provider"), model_metadata={})
    with pytest.raises(PermissionError):
        run_evaluation(dataset_path=holdout, service=service, runtime_image=IMAGE, output=tmp_path / "denied",
                       limits=RunLimits(), max_total_turns=1, max_total_tool_calls=1, development_reference=dataset)
