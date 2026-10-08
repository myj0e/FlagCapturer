"""Dependency-free fake-provider loop with append-only events and a private oracle."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SYNTHETIC_FLAG = "CTFBOT_SYNTHETIC{phase_a_verifier_demo}"


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ModelTurn:
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()


class FakeModel:
    """Return a fixed synthetic transcript; never reads the private oracle."""

    def __init__(self) -> None:
        self.turn = 0

    def complete(self, history: list[dict[str, Any]]) -> ModelTurn:
        self.turn += 1
        if self.turn == 1:
            return ModelTurn(tool_calls=(ToolCall("workspace.list", {"path": "."}),))
        if self.turn == 2:
            return ModelTurn(tool_calls=(ToolCall("workspace.read_text", {"path": "challenge.txt"}),))
        if self.turn == 3:
            return ModelTurn(tool_calls=(ToolCall("workspace.read_text", {"path": "../outside.txt"}),))
        if self.turn == 4:
            challenge_text = next(
                (
                    item.get("result", {}).get("text", "")
                    for item in reversed(history)
                    if item.get("role") == "tool"
                    and isinstance(item.get("result"), dict)
                    and "text" in item["result"]
                ),
                "",
            )
            match = re.search(r"CTFBOT_SYNTHETIC\{[^}\r\n]+\}", challenge_text)
            candidate = match.group(0) if match else ""
            return ModelTurn(tool_calls=(ToolCall("candidate.submit", {"candidate": candidate}),))
        return ModelTurn(text="Demo complete; synthetic oracle verified.")


class EventLog:
    """Append JSONL records immediately so an interrupted run keeps its trace."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.events: list[dict[str, Any]] = []
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")

    def append(self, event_type: str, **fields: Any) -> None:
        record = {
            "seq": len(self.events) + 1,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            **fields,
        }
        rendered = json.dumps(record, ensure_ascii=False, sort_keys=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(rendered + "\n")
            stream.flush()
        self.events.append(record)

    def render(self) -> str:
        return "\n".join(json.dumps(event, ensure_ascii=False, sort_keys=True) for event in self.events)


def checked_path(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise PermissionError("path must stay inside the challenge workspace")
    resolved_root = root.resolve(strict=True)
    resolved_path = (resolved_root / path).resolve(strict=True)
    if not resolved_path.is_relative_to(resolved_root):
        raise PermissionError("resolved path escapes the challenge workspace")
    return resolved_path


def safe_call_summary(call: ToolCall) -> dict[str, Any]:
    serialized = json.dumps(call.arguments, ensure_ascii=False, sort_keys=True)
    summary: dict[str, Any] = {
        "tool": call.name,
        "arguments_sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
    }
    if call.name == "candidate.submit":
        candidate = call.arguments.get("candidate")
        summary["arguments"] = {
            "candidate": "[redacted]",
            "candidate_sha256": hashlib.sha256(str(candidate).encode("utf-8")).hexdigest(),
        }
    else:
        summary["arguments"] = call.arguments
    return summary


def dispatch_tool(
    root: Path,
    call: ToolCall,
    *,
    oracle_path: Path,
    evidence_dir: Path,
    evidence_id: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if call.name == "workspace.list":
        if set(call.arguments) - {"path"}:
            raise ValueError("workspace.list accepts only the path argument")
        relative = call.arguments.get("path", ".")
        if not isinstance(relative, str):
            raise ValueError("workspace.list path must be a string")
        target = checked_path(root, relative)
        if not target.is_dir():
            raise ValueError("path is not a directory")
        entries = sorted(child.name for child in target.iterdir())
        return {"entries": entries}, {"summary": f"listed {len(entries)} entries"}

    if call.name == "workspace.read_text":
        if set(call.arguments) != {"path"} or not isinstance(call.arguments.get("path"), str):
            raise ValueError("workspace.read_text requires one string path")
        target = checked_path(root, call.arguments["path"])
        if not target.is_file():
            raise ValueError("path is not a regular file")
        if target.stat().st_size > 4096:
            raise ValueError("file exceeds the 4096-byte spike limit")
        text = target.read_text(encoding="utf-8")
        artifact = evidence_dir / f"{evidence_id:04d}-read-text.txt"
        artifact.write_text(text, encoding="utf-8")
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        return (
            {"text": text, "artifact_ref": artifact.name, "sha256": digest},
            {"summary": f"read {len(text.encode('utf-8'))} bytes", "artifact_ref": artifact.name, "sha256": digest},
        )

    if call.name == "candidate.submit":
        if set(call.arguments) != {"candidate"} or not isinstance(call.arguments.get("candidate"), str):
            raise ValueError("candidate.submit requires one string candidate")
        candidate = call.arguments["candidate"]
        if len(candidate.encode("utf-8")) > 256:
            raise ValueError("candidate exceeds the spike limit")
        oracle = json.loads(oracle_path.read_text(encoding="utf-8"))
        expected = oracle.get("flag")
        if not isinstance(expected, str) or not expected:
            raise ValueError("synthetic oracle is invalid")
        accepted = hmac.compare_digest(candidate, expected)
        digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
        return (
            {"status": "verified" if accepted else "rejected"},
            {"status": "verified" if accepted else "rejected", "candidate_sha256": digest},
        )

    raise ValueError(f"tool is not registered: {call.name}")


def run() -> tuple[str, EventLog]:
    with tempfile.TemporaryDirectory(prefix="ctfbot-direct-loop-") as scratch:
        run_root = Path(scratch)
        workspace = run_root / "workspace"
        private = run_root / "private"
        evidence_dir = run_root / "evidence"
        workspace.mkdir(mode=0o700)
        private.mkdir(mode=0o700)
        evidence_dir.mkdir(mode=0o700)
        (workspace / "challenge.txt").write_text(
            f"Synthetic challenge fixture. Candidate marker: {SYNTHETIC_FLAG}\n",
            encoding="utf-8",
        )
        oracle_path = private / "oracle.json"
        oracle_path.write_text(json.dumps({"flag": SYNTHETIC_FLAG}), encoding="utf-8")
        os.chmod(oracle_path, 0o600)

        events = EventLog(run_root / "events.jsonl")
        events.append("run_started", challenge_id="synthetic-stage-a", oracle_access="controller_only")
        model = FakeModel()
        history: list[dict[str, Any]] = []
        max_turns = 6
        max_tool_calls = 5
        tool_calls = 0
        final_text = ""
        run_status = "candidate_unverified"

        for turn_number in range(1, max_turns + 1):
            turn = model.complete(history)
            safe_calls = [safe_call_summary(call) for call in turn.tool_calls]
            events.append(
                "model_turn",
                turn=turn_number,
                text=turn.text,
                tool_calls=safe_calls,
                usage={"input_tokens": None, "output_tokens": None, "source": "fake"},
            )
            history.append({"role": "assistant", "turn": {"text": turn.text, "tool_calls": safe_calls}})
            if not turn.tool_calls:
                final_text = turn.text
                break

            for call in turn.tool_calls:
                tool_calls += 1
                if tool_calls > max_tool_calls:
                    events.append("budget_denied", budget="max_tool_calls")
                    final_text = "Tool-call budget exhausted."
                    break
                call_summary = safe_call_summary(call)
                try:
                    tool_result, evidence = dispatch_tool(
                        workspace,
                        call,
                        oracle_path=oracle_path,
                        evidence_dir=evidence_dir,
                        evidence_id=tool_calls,
                    )
                    serialized = json.dumps(tool_result, ensure_ascii=False, sort_keys=True)
                    response = {
                        "status": "ok",
                        "summary": evidence.get("summary", evidence.get("status", "completed")),
                        "result_sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
                        **{key: value for key, value in evidence.items() if key != "summary"},
                    }
                    if tool_result.get("status") == "verified":
                        run_status = "verified"
                    elif tool_result.get("status") == "rejected":
                        run_status = "candidate_unverified"
                except (OSError, UnicodeError, ValueError) as exc:
                    tool_result = {"status": "denied" if isinstance(exc, PermissionError) else "error"}
                    response = {"status": tool_result["status"], "summary": str(exc)}

                events.append("tool_result", **call_summary, **response)
                history.append({"role": "tool", "name": call.name, "result": tool_result})
            else:
                continue
            break

        events.append("run_finished", status=run_status, final_text=final_text, tool_calls=tool_calls)
        return final_text, events


if __name__ == "__main__":
    answer, event_log = run()
    print(f"Final: {answer}")
    print("Events:")
    print(event_log.render())
