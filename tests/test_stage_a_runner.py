from __future__ import annotations

import json
import hashlib
import os
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from typing import Any
from unittest.mock import patch

from ctfbot.agent.loop import AgentLoop, RunLimits
from ctfbot.application.baseline_smoke import run_synthetic_smoke
from ctfbot.application.baseline import BaselineAdmissionError, run_baseline
from ctfbot.evidence.store import EvidenceStore
from ctfbot.evidence.redaction import redact_sensitive_text
from ctfbot.model_adapters.codex_app_server import CodexAppServer
from ctfbot.model_adapters.protocol import FakeModelSession, ScriptedTurn, ToolCall
from ctfbot.runtime.docker import docker_create_argv
from ctfbot.tools.registry import ToolRegistry
from ctfbot.tools.registry import CommandResult


class DynamicToolProtocolTests(unittest.TestCase):
    def test_dynamic_tool_round_trip_normalizes_events_without_delta_duplication(self) -> None:
        thread_id = "thread-1"
        turn_id = "turn-1"
        messages = [
            {
                "method": "item/tool/call",
                "id": 9,
                "params": {
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "tool": "challenge_list",
                    "arguments": {"path": "."},
                },
            },
            {
                "method": "item/agentMessage/delta",
                "params": {"threadId": thread_id, "turnId": turn_id, "delta": "partial text"},
            },
            {
                "method": "item/completed",
                "params": {"threadId": thread_id, "turnId": turn_id,
                           "item": {"type": "reasoning", "text": "private content must not be forwarded"}},
            },
            {
                "method": "item/completed",
                "params": {
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "item": {"type": "agentMessage", "text": "final text"},
                },
            },
            {
                "method": "turn/completed",
                "params": {"turn": {"id": turn_id, "status": "completed", "usage": {"inputTokens": 17}}},
            },
        ]

        class StubServer(CodexAppServer):
            def __init__(self) -> None:
                super().__init__(executable="codex")
                self.sent: list[dict[str, Any]] = []

            def request(self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 30) -> dict[str, Any]:
                self.assert_turn_request = method == "turn/start" and params is not None
                return {"turn": {"id": turn_id}}

            def _take_dynamic_turn_message(self, requested_thread: str, requested_turn: str,
                                           timeout: float) -> dict[str, Any]:
                assert requested_thread == thread_id
                assert requested_turn == turn_id
                assert timeout > 0
                return messages.pop(0)

            def _send(self, message: dict[str, Any]) -> None:
                self.sent.append(message)

        server = StubServer()
        handled: list[ToolCall] = []

        def handle(call: dict[str, Any]) -> dict[str, Any]:
            handled.append(ToolCall(call["name"], call["arguments"], call["call_id"]))
            return {"content": "listed", "success": True}

        public_messages = []
        result = server.run_dynamic_turn(thread_id, "gpt-test", "list files", [], handle, timeout=5,
                                         on_message=public_messages.append)
        self.assertEqual(public_messages, ["final text"])
        self.assertTrue(server.assert_turn_request)
        self.assertEqual(result["text"], "final text")
        self.assertEqual(result["usage"], {"inputTokens": 17})
        self.assertEqual(result["tool_calls"], 1)
        self.assertEqual(handled, [ToolCall("challenge_list", {"path": "."}, "9")])
        self.assertEqual(server.sent[0]["result"]["contentItems"][0]["text"], "listed")


class WorkspacePolicyTests(unittest.TestCase):
    def test_traversal_and_symlink_escape_are_denied(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            challenge = root / "challenge"
            work = root / "work"
            private = root / "private"
            challenge.mkdir()
            work.mkdir()
            private.mkdir()
            (challenge / "input.txt").write_text("safe", encoding="utf-8")
            (private / "private_data.json").write_text('{"flag":"hidden"}', encoding="utf-8")
            os.chmod(private, 0o700)
            os.chmod(private / "private_data.json", 0o600)
            (challenge / "private_data-link").symlink_to(private / "private_data.json")
            evidence = EvidenceStore(root / "run")
            registry = ToolRegistry(challenge, work, evidence, runtime=None)

            traversal = registry.invoke(ToolCall("challenge_read_text", {"path": "../private/private_data.json"}))
            symlink = registry.invoke(ToolCall("challenge_read_text", {"path": "private_data-link"}))
            self.assertEqual(traversal.result["status"], "policy_denied")
            self.assertEqual(symlink.result["status"], "policy_denied")
            self.assertNotIn("hidden", traversal.reply.content + symlink.reply.content)

    def test_oversized_command_response_remains_valid_json_and_points_to_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            challenge = root / "challenge"
            work = root / "work"
            challenge.mkdir()
            work.mkdir()
            evidence = EvidenceStore(root / "run")

            class LargeOutputRuntime:
                def execute(self, argv: list[str], *, timeout: float) -> CommandResult:
                    del argv, timeout
                    return CommandResult(0, ("value\"\\\n" * 2000).encode(), b"stderr")

            registry = ToolRegistry(
                challenge, work, evidence, LargeOutputRuntime(),
                max_model_output_bytes=512,
            )
            outcome = registry.invoke(ToolCall("command_run", {"argv": ["print"]}))
            payload = json.loads(outcome.reply.content)
            self.assertTrue(payload["truncated"])
            self.assertLessEqual(len(outcome.reply.content.encode()), 512)
            self.assertTrue((evidence.run_dir / payload["stdout_evidence"]).is_file())


class DockerProfileTests(unittest.TestCase):
    def test_create_profile_is_pinned_offline_read_only_and_unprivileged(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image = "registry.example/ctf-tools@sha256:" + "a" * 64
            command = docker_create_argv(
                image_digest=image,
                challenge_root=root,
                container_name="ctfbot-12345678-1234-1234-1234-123456789012",
            )
        rendered = " ".join(command)
        for token in ("--network=none", "--read-only", "--cap-drop=ALL", "no-new-privileges:true",
                      "--pids-limit", "target=/challenge,readonly", "/work:rw,exec"):
            self.assertIn(token, rendered)
        self.assertNotIn("docker.sock", rendered)
        self.assertNotIn("--privileged", rendered)
        self.assertEqual(command[-2], "-c")
        self.assertEqual(command[-1], "while :; do sleep 3600; done")

    def test_mutable_image_tags_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                docker_create_argv(
                    image_digest="registry.example/ctf-tools:latest",
                    challenge_root=Path(temp),
                    container_name="ctfbot-12345678-1234-1234-1234-123456789012",
                )

    def test_immutable_local_image_id_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            command = docker_create_argv(
                image_digest="sha256:" + "b" * 64,
                challenge_root=Path(temp),
                container_name="ctfbot-12345678-1234-1234-1234-123456789012",
            )
        self.assertEqual(command[-3], "sha256:" + "b" * 64)

    def test_root_controller_is_rejected_by_the_nonroot_container_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with patch("ctfbot.runtime.docker.os.getuid", return_value=0):
                with self.assertRaisesRegex(ValueError, "non-root controller"):
                    docker_create_argv(
                        image_digest="sha256:" + "b" * 64,
                        challenge_root=Path(temp),
                        container_name="ctfbot-12345678-1234-1234-1234-123456789012",
                    )


class BaselineInputExposureTests(unittest.TestCase):
    def test_tools_and_container_receive_only_the_input_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            input_root = workspace / "input"
            input_root.mkdir(parents=True, mode=0o700)
            os.chmod(workspace, 0o700)
            input_bytes = b"CTFBOT_SYNTHETIC_INPUT_ONLY"
            (input_root / "ciphertext.txt").write_bytes(input_bytes)
            os.chmod(input_root / "ciphertext.txt", 0o444)
            os.chmod(input_root, 0o555)

            task = "Synthetic task sentinel; never send this fixture externally.\n"
            (workspace / "TASK.md").write_text(task, encoding="utf-8")
            os.chmod(workspace / "TASK.md", 0o444)
            challenge_id = "synthetic-input-exposure"
            metadata_hash = hashlib.sha256(b"synthetic challenge metadata").hexdigest()
            provenance = {
                "challenge_id": challenge_id,
                "source_commit": "synthetic-commit",
                "challenge_metadata_sha256": metadata_hash,
                "formal_admission": "admitted",
                "import_mode": "static_files_only",
                "authorization_scope": "private local evaluation; do not redistribute challenge assets",
                "model_data_authorized": True,
                "model_data_authorization_basis": "synthetic local-only fixture",
                "task_sha256": hashlib.sha256(task.encode("utf-8")).hexdigest(),
                "input_files": [{
                    "path": "ciphertext.txt",
                    "bytes": len(input_bytes),
                    "sha256": hashlib.sha256(input_bytes).hexdigest(),
                }],
            }
            (workspace / "provenance.json").write_text(json.dumps(provenance), encoding="utf-8")
            os.chmod(workspace / "provenance.json", 0o444)
            private = root / "private"
            private.mkdir(mode=0o700)
            private_data = private / "private_data.json"
            private_data.write_text(json.dumps({
                "challenge_id": challenge_id,
                "source_commit": "synthetic-commit",
                "challenge_metadata_sha256": metadata_hash,
                "flag": "CTFBOT_SYNTHETIC{candidate}",
            }), encoding="utf-8")
            os.chmod(private_data, 0o600)

            class CapturingRuntime:
                instances: list[CapturingRuntime] = []

                def __init__(self, challenge_root: Path, image_digest: str, **kwargs: Any) -> None:
                    del kwargs
                    self.challenge_root = challenge_root.resolve(strict=True)
                    self.argv = docker_create_argv(
                        image_digest=image_digest,
                        challenge_root=challenge_root,
                        container_name="ctfbot-12345678-1234-1234-1234-123456789012",
                    )
                    self.closed = False
                    self.__class__.instances.append(self)

                def __enter__(self) -> CapturingRuntime:
                    return self

                def __exit__(self, *_: object) -> None:
                    self.closed = True

                def execute(self, argv: list[str], *, timeout: float) -> CommandResult:
                    del timeout
                    if len(argv) == 2 and argv[0] == "cat" and argv[1].startswith("/challenge/"):
                        relative = argv[1].removeprefix("/challenge/")
                        path = PurePosixPath(relative)
                        if path.is_absolute() or ".." in path.parts:
                            return CommandResult(1, b"", b"not found")
                        target = self.challenge_root / relative
                        if target.is_file() and not target.is_symlink():
                            return CommandResult(0, target.read_bytes(), b"")
                        return CommandResult(1, b"", b"not found")
                    return CommandResult(2, b"", b"unexpected synthetic command")

            model = FakeModelSession((ScriptedTurn(tool_calls=(
                ToolCall("challenge_list", {"path": "."}),
                ToolCall("challenge_read_text", {"path": "ciphertext.txt"}),
                ToolCall("challenge_read_text", {"path": "../provenance.json"}),
                ToolCall("challenge_read_text", {"path": "../TASK.md"}),
                ToolCall("command_run", {"argv": ["cat", "/challenge/provenance.json"]}),
                ToolCall("command_run", {"argv": ["cat", "/challenge/TASK.md"]}),
                ToolCall("command_run", {"argv": ["cat", "/challenge/private/private_data.json"]}),
                ToolCall("command_run", {"argv": ["cat", "/challenge/../TASK.md"]}),
                ToolCall("command_run", {"argv": ["cat", "/challenge/../../private/private_data.json"]}),
                ToolCall("candidate_submit", {"candidate": "CTFBOT_SYNTHETIC{candidate}"}),
                ToolCall("run_complete", {"outcome": "candidate_unverified", "candidate_id": "candidate-1", "summary": "Retain candidate", "unresolved": []}),
            )),))
            image = "sha256:" + "a" * 64
            with patch("ctfbot.application.baseline.DockerRuntime", CapturingRuntime):
                result = run_baseline(
                    workspace=workspace,
                    runtime_image=image,
                    runs_root=root / "runs",
                    model=model,
                    model_metadata={"provider": "fake"},
                    limits=RunLimits(max_turns=1, max_tool_calls=12),
                )

            self.assertEqual(result.status, "candidate_unverified")
            self.assertTrue(CapturingRuntime.instances[0].closed)
            runtime = CapturingRuntime.instances[0]
            self.assertEqual(runtime.challenge_root, input_root.resolve())
            mount = next(arg for arg in runtime.argv if arg.startswith("type=bind,"))
            self.assertEqual(mount, f"type=bind,source={input_root.resolve()},target=/challenge,readonly")

            events = [
                json.loads(line)
                for line in (Path(result.run_dir) / "events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            calls = [event for event in events if event["event_type"] == "tool_call"]
            outcomes = [event for event in events if event["event_type"] == "tool_result"]
            self.assertEqual([event["name"] for event in calls], [
                "challenge_list", "challenge_read_text", "challenge_read_text", "challenge_read_text",
                "command_run", "command_run", "command_run", "command_run", "command_run", "candidate_submit", "run_complete",
            ])
            self.assertEqual([event["result"]["status"] for event in outcomes], [
                "ok", "ok", "policy_denied", "policy_denied",
                "command_failed", "command_failed", "command_failed", "command_failed", "command_failed", "unverified", "run_complete",
            ])
            listing_ref = outcomes[0]["response_evidence"]["artifact"]
            listing = json.loads((Path(result.run_dir) / listing_ref).read_text(encoding="utf-8"))
            self.assertEqual([{key: item[key] for key in ('name','kind')} for item in listing['entries']],
                             [{"name": "ciphertext.txt", "kind": "file"}])
            self.assertEqual(listing['entries'][0]['format_hint'], 'UTF-8 text prefix')


class OfflineAcceptanceTests(unittest.TestCase):
    def test_synthetic_agent_loop_completes_without_provider_or_docker(self) -> None:
        passed, _ = run_synthetic_smoke()
        self.assertTrue(passed)

    def test_unadmitted_snapshot_fails_before_provider_turn_or_docker(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            (workspace / "input").mkdir(parents=True, mode=0o700)
            (workspace / "TASK.md").write_text("synthetic", encoding="utf-8")
            (workspace / "provenance.json").write_text(json.dumps({
                "challenge_id": "pending-case",
                "formal_admission": "not_admitted",
                "import_mode": "static_files_only",
                "authorization_scope": "private local evaluation; do not redistribute challenge assets",
            }), encoding="utf-8")
            private = root / "private"
            private.mkdir(mode=0o700)
            private_data = private / "private_data.json"
            private_data.write_text('{"flag":"synthetic"}', encoding="utf-8")
            os.chmod(private_data, 0o600)
            model = FakeModelSession(())
            with self.assertRaises(BaselineAdmissionError):
                run_baseline(
                    workspace=workspace,
                    runtime_image="registry.example/tool@sha256:" + "a" * 64,
                    runs_root=root / "runs",
                    model=model,
                    model_metadata={"provider": "fake"},
                )
            self.assertFalse(model.closed)

    def test_private_local_authorization_does_not_allow_model_data_transmission(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            input_root = workspace / "input"
            input_root.mkdir(parents=True, mode=0o700)
            os.chmod(workspace, 0o700)
            input_data = b"private synthetic attachment"
            (input_root / "input.txt").write_bytes(input_data)
            os.chmod(input_root / "input.txt", 0o444)
            os.chmod(input_root, 0o555)
            task = "# Challenge task\n\nSynthetic task.\n"
            (workspace / "TASK.md").write_text(task, encoding="utf-8")
            os.chmod(workspace / "TASK.md", 0o444)
            challenge_id = "synthetic-private-only"
            metadata_hash = hashlib.sha256(b"synthetic metadata").hexdigest()
            provenance = {
                "challenge_id": challenge_id,
                "source_commit": "synthetic-commit",
                "challenge_metadata_sha256": metadata_hash,
                "formal_admission": "admitted",
                "import_mode": "static_files_only",
                "authorization_scope": "private local evaluation; do not redistribute challenge assets",
                "model_data_authorized": False,
                "model_data_authorization_basis": None,
                "task_sha256": hashlib.sha256(task.encode()).hexdigest(),
                "input_files": [{
                    "path": "input.txt",
                    "bytes": len(input_data),
                    "sha256": hashlib.sha256(input_data).hexdigest(),
                }],
            }
            (workspace / "provenance.json").write_text(json.dumps(provenance), encoding="utf-8")
            os.chmod(workspace / "provenance.json", 0o444)
            private = root / "private"
            private.mkdir(mode=0o700)
            private_data = private / "private_data.json"
            private_data.write_text(json.dumps({
                "challenge_id": challenge_id,
                "source_commit": "synthetic-commit",
                "challenge_metadata_sha256": metadata_hash,
                "flag": "synthetic",
            }), encoding="utf-8")
            os.chmod(private_data, 0o600)
            model = FakeModelSession(())
            with self.assertRaisesRegex(BaselineAdmissionError, "transmission to a model"):
                run_baseline(
                    workspace=workspace,
                    runtime_image="registry.example/tool@sha256:" + "a" * 64,
                    runs_root=root / "runs",
                    model=model,
                    model_metadata={"provider": "fake"},
                )
            self.assertFalse(model.closed)
            self.assertFalse((root / "runs").exists())

    def test_error_trace_redacts_common_credential_shapes(self) -> None:
        value = 'Bearer abc.def api_key="secret-value" refresh_token=other-secret'
        safe = redact_sensitive_text(value)
        self.assertNotIn("abc.def", safe)
        self.assertNotIn("secret-value", safe)
        self.assertNotIn("other-secret", safe)

    def test_explicit_completion_stops_later_tool_calls_in_the_same_turn(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            challenge = root / "challenge"
            work = root / "work"
            challenge.mkdir(mode=0o700)
            work.mkdir(mode=0o700)
            evidence = EvidenceStore(root / "run")

            class CountingRuntime:
                def __init__(self) -> None:
                    self.calls: list[list[str]] = []

                def execute(self, argv: list[str], *, timeout: float) -> CommandResult:
                    del timeout
                    self.calls.append(argv)
                    return CommandResult(0, b"unexpected", b"")

            runtime = CountingRuntime()
            model = FakeModelSession((ScriptedTurn(tool_calls=(
                ToolCall("candidate_submit", {"candidate": "CTFBOT_SYNTHETIC{valid}"}),
                    ToolCall("run_complete", {"outcome": "candidate_unverified", "candidate_id": "candidate-1", "summary": "Retain candidate", "unresolved": []}),
                ToolCall("command_run", {"argv": ["touch", "should-not-run"]}),
            )),))
            registry = ToolRegistry(challenge, work, evidence, runtime)
            result = AgentLoop(model, registry, evidence, limits=RunLimits(max_turns=2)).run("synthetic task")
            self.assertEqual(result.status, "candidate_unverified")
            self.assertEqual(runtime.calls, [])
            events = [json.loads(line) for line in (root / "run" / "events.jsonl").read_text().splitlines()]
            statuses = [event.get("result", {}).get("status") for event in events if event["event_type"] == "tool_result"]
            self.assertEqual(statuses, ["unverified", "run_complete", "already_completed"])


if __name__ == "__main__":
    unittest.main()
