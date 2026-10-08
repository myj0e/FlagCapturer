"""Offline synthetic acceptance path for the Stage A agent loop."""

from __future__ import annotations

import json
import hashlib
import os
import tempfile
from pathlib import Path

from ctfbot.agent.loop import AgentLoop, RunLimits
from ctfbot.application.baseline import validate_baseline_snapshot
from ctfbot.evidence.store import EvidenceStore
from ctfbot.model_adapters.protocol import FakeModelSession, ScriptedTurn, ToolCall
from ctfbot.tools.registry import CommandResult, ToolRegistry


class _SyntheticRuntime:
    def execute(self, argv: list[str], *, timeout: float) -> CommandResult:
        del timeout
        return CommandResult(0, ("synthetic runtime: " + " ".join(argv)).encode(), b"")


def run_synthetic_smoke() -> tuple[bool, str]:
    """Exercise tool dispatch, traversal denial, evidence, and exact verification offline."""
    expected = "CTFBOT_SYNTHETIC{phase_a_acceptance}"
    with tempfile.TemporaryDirectory(prefix="ctfbot-stage-a-smoke-") as temp:
        root = Path(temp)
        challenge = root / "challenge"
        work = root / "controller-work"
        private = root / "private"
        run_root = root / "runs"
        challenge.mkdir(mode=0o700)
        work.mkdir(mode=0o700)
        private.mkdir(mode=0o700)
        input_root = challenge / "input"
        input_root.mkdir(mode=0o700)
        input_data = b"Synthetic fixture; never a real CTF challenge."
        input_path = input_root / "input.txt"
        input_path.write_bytes(input_data)
        os.chmod(input_path, 0o444)
        os.chmod(input_root, 0o555)
        task = "# Challenge task\n\nRecover the synthetic test candidate.\n"
        (challenge / "TASK.md").write_text(task, encoding="utf-8")
        os.chmod(challenge / "TASK.md", 0o444)
        oracle = private / "oracle.json"
        metadata_hash = hashlib.sha256(b"synthetic-metadata").hexdigest()
        oracle.write_text(json.dumps({
            "challenge_id": "synthetic-stage-a",
            "source_commit": "synthetic-commit",
            "challenge_metadata_sha256": metadata_hash,
            "flag": expected,
        }), encoding="utf-8")
        os.chmod(oracle, 0o600)
        provenance = {
            "challenge_id": "synthetic-stage-a",
            "source_commit": "synthetic-commit",
            "challenge_metadata_sha256": metadata_hash,
            "category": "synthetic",
            "formal_admission": "admitted",
            "import_mode": "static_files_only",
            "authorization_scope": "private local evaluation; do not redistribute challenge assets",
            "task_sha256": hashlib.sha256(task.encode("utf-8")).hexdigest(),
            "input_files": [{
                "path": "input.txt",
                "bytes": len(input_data),
                "sha256": hashlib.sha256(input_data).hexdigest(),
            }],
        }
        (challenge / "provenance.json").write_text(json.dumps(provenance), encoding="utf-8")
        os.chmod(challenge / "provenance.json", 0o444)
        validate_baseline_snapshot(challenge, oracle, run_root)
        evidence = EvidenceStore(run_root / "synthetic-run")
        model = FakeModelSession((
            ScriptedTurn(tool_calls=(ToolCall("challenge_list", {"path": "."}),)),
            ScriptedTurn(tool_calls=(ToolCall("challenge_read_text", {"path": "input/input.txt"}),)),
            ScriptedTurn(tool_calls=(ToolCall("challenge_read_text", {"path": "../private/oracle.json"}),)),
            ScriptedTurn(tool_calls=(ToolCall("command_run", {"argv": ["synthetic", "check"]}),)),
            ScriptedTurn(tool_calls=(ToolCall("candidate_submit", {"candidate": expected}),)),
        ))
        registry = ToolRegistry(challenge, work, evidence, _SyntheticRuntime(), oracle)
        result = AgentLoop(
            model,
            registry,
            evidence,
            limits=RunLimits(max_turns=6, max_tool_calls=8, wall_time_seconds=30),
            challenge_id="synthetic-stage-a",
        ).run(task)
        records = [json.loads(line) for line in (run_root / "synthetic-run" / "events.jsonl").read_text().splitlines()]
        denied = any(event.get("event_type") == "tool_result" and event.get("result", {}).get("status") == "policy_denied" for event in records)
        return result.verified and result.status == "verified" and denied, "temporary synthetic evidence was checked and cleaned up"
