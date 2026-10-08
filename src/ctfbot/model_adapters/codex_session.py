"""Codex App Server implementation of the provider-neutral session contract."""

from __future__ import annotations

import tempfile
import hashlib
import json
import threading
from collections.abc import Callable, Sequence
from typing import Any

from ctfbot.model_adapters.codex_app_server import CodexAppServer, CodexAppServerError, find_codex_cli
from ctfbot.model_adapters.events import CODEX_CONTEXT_CAPABILITIES
from ctfbot.model_adapters.protocol import ModelSession, ToolCall, ToolHandler, ToolReply, ToolSpec, TurnResult


class CodexModelSession(ModelSession):
    """A single model thread using experimental dynamic tools from Codex CLI."""

    context_capabilities = CODEX_CONTEXT_CAPABILITIES

    def __init__(self, model: str, effort: str | None = None, *, executable: str | None = None,
                 timeout: float = 1800) -> None:
        self.model = model
        self.effort = effort
        self.timeout = timeout
        self._temp = tempfile.TemporaryDirectory(prefix="ctfbot-codex-session-")
        self._server = CodexAppServer(executable or find_codex_cli(), experimental_api=True)
        self._thread_id: str | None = None
        self._tool_schema_hash: str | None = None
        self._closed = False
        self._cancelled = threading.Event()
        self.turns_started = 0
        self._event_handler: Callable[[dict], None] | None = None
        self._message_handler: Callable[[str], None] | None = None
        try:
            self._server.start()
            account = self._server.account()
            if account.get("type") != "chatgpt":
                raise CodexAppServerError("The Codex CLI is not authenticated with a ChatGPT account.")
        except Exception:
            self.close()
            raise

    def set_event_handler(self, handler: Callable[[dict], None]) -> None:
        self._event_handler = handler

    def set_message_handler(self, handler: Callable[[str], None]) -> None:
        """Observe public agentMessage items, never private reasoning content."""
        self._message_handler = handler

    def run_turn(self, prompt: str, tools: Sequence[ToolSpec], on_tool_call: ToolHandler,
                 *, timeout: float) -> TurnResult:
        if self._closed:
            raise RuntimeError("Codex model session is closed")
        if self._cancelled.is_set():
            raise RuntimeError("Codex model session was cancelled")
        schemas = [tool.as_provider_schema() for tool in tools]
        schema_hash = hashlib.sha256(json.dumps(schemas, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if self._thread_id is None:
            self._thread_id = self._server.start_dynamic_thread(
                self.model,
                self._temp.name,
                schemas,
                timeout=min(timeout, self.timeout),
            )
            self._tool_schema_hash = schema_hash
        elif schema_hash != self._tool_schema_hash:
            raise ValueError("tool schema cannot change within a Codex model session")

        def handle(raw: dict[str, Any]) -> dict[str, Any]:
            arguments = raw.get("arguments")
            call = ToolCall(
                name=str(raw.get("name", "")),
                arguments=arguments if isinstance(arguments, dict) else {},
                call_id=str(raw.get("call_id", "")) or None,
            )
            reply: ToolReply = on_tool_call(call)
            return {"content": reply.content, "success": reply.success}

        event_handler = getattr(self, "_event_handler", None)
        event_options = {"on_event": event_handler} if event_handler else {}
        self.turns_started += 1
        try:
            result = self._server.run_dynamic_turn(
                self._thread_id,
                self.model,
                prompt,
                schemas,
                handle,
                cwd=self._temp.name,
                effort=self.effort,
                timeout=min(timeout, self.timeout),
                on_message=self._message_handler,
                **event_options,
            )
        except CodexAppServerError as exc:
            if "timed out" in str(exc).lower() or "timeout" in str(exc).lower():
                raise TimeoutError("Codex model turn timed out") from exc
            raise
        usage = result.get("usage")
        return TurnResult(
            text=str(result.get("text", "")),
            usage=usage if isinstance(usage, dict) else None,
            response_id=result.get("response_id"),
            finish_reason=result.get("finish_reason"),
            tool_calls=int(result.get("tool_calls", 0)),
        )

    def cancel(self) -> None:
        """Abort the active provider turn by terminating this session's App Server."""
        self._cancelled.set()
        self._server.interrupt()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._thread_id:
            try:
                self._server.delete_thread(self._thread_id)
            except CodexAppServerError:
                pass
        self._server.close()
        self._temp.cleanup()
