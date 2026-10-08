"""Verify the dynamic-tool event collector with a local fake JSON-RPC stream."""

from __future__ import annotations

import sys
from collections import deque
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from ctfbot.model_adapters.codex_app_server import CodexAppServer  # noqa: E402


def main() -> None:
    server = CodexAppServer(executable="fake-codex", experimental_api=True)
    notifications: deque[dict[str, Any]] = deque()
    unmatched: deque[dict[str, Any]] = deque()
    sent: list[dict[str, Any]] = []
    server._notifications = notifications
    server._unmatched = unmatched
    server._send = lambda message: sent.append(message)  # type: ignore[method-assign]

    messages = iter([
        {
            "method": "item/tool/call",
            "id": 101,
            "params": {
                "callId": "call-1",
                "threadId": "thread-1",
                "turnId": "turn-1",
                "tool": "ctfbot_probe",
                "arguments": {},
            },
        },
        {
            "method": "item/agentMessage/delta",
            "params": {"threadId": "thread-1", "turnId": "turn-1", "delta": "CTFBOT_TOOL_ROUNDTRIP_OK"},
        },
        {
            "method": "item/completed",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "item": {"type": "agentMessage", "text": "CTFBOT_TOOL_ROUNDTRIP_OK"},
            },
        },
        {
            "method": "turn/completed",
            "params": {
                "turn": {
                    "id": "turn-1",
                    "status": "completed",
                    "usage": {"inputTokens": 1, "outputTokens": 1},
                }
            },
        },
    ])
    server._next_message = lambda timeout: next(messages)  # type: ignore[method-assign]

    requests: list[str] = []

    def fake_request(method: str, params: dict[str, Any] | None = None, *, timeout: float = 30) -> dict[str, Any]:
        del timeout
        requests.append(method)
        if method == "thread/start":
            return {"thread": {"id": "thread-1"}}
        if method == "turn/start":
            return {"turn": {"id": "turn-1"}}
        return {}

    server.request = fake_request  # type: ignore[method-assign]
    result = server.test_dynamic_tool("fake-model")

    assert requests == ["thread/start", "turn/start", "thread/delete"]
    assert result == {
        "tool": "ctfbot_probe",
        "arguments": {},
        "response": "CTFBOT_TOOL_ROUNDTRIP_OK",
        "usage": {"inputTokens": 1, "outputTokens": 1},
    }
    assert len(sent) == 1
    assert sent[0]["id"] == 101
    assert sent[0]["result"]["success"] is True
    assert sent[0]["result"]["contentItems"] == [{"type": "inputText", "text": "CTFBOT_TOOL_OK"}]
    print("fake dynamic-tool protocol passed: one callback, one result, deduplicated final text, usage capture")


if __name__ == "__main__":
    main()
