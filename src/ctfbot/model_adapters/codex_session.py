"""Codex App Server implementation of the provider-neutral session contract."""

from __future__ import annotations

import tempfile
import base64
import hashlib
import json
import threading
from collections.abc import Callable, Sequence
from typing import Any

from ctfbot.model_adapters.codex_app_server import CodexAppServer, CodexAppServerError, find_codex_cli
from ctfbot.model_adapters.events import CODEX_CONTEXT_CAPABILITIES
from ctfbot.model_adapters.protocol import ModelSession, ToolCall, ToolHandler, ToolReply, ToolSpec, TurnResult


def model_supports_images(models: Sequence[dict[str, Any]], selected: str) -> bool:
    """Use provider capabilities, never a model-name allowlist.

    Codex's catalog defaults omitted inputModalities to text+image for older
    entries. An unknown model or explicit text-only entry stays disabled.
    """
    entry = next((item for item in models if selected in {item.get("id"), item.get("model")}), None)
    if entry is None:
        return False
    modalities = entry.get("inputModalities", ["text", "image"])
    return isinstance(modalities, list) and "image" in modalities


def encode_tool_reply(reply: ToolReply, *, supports_images: bool) -> dict[str, Any]:
    """Map neutral attachments to Codex dynamic-tool content items."""
    if reply.images and not supports_images:
        return {"content": "Image input is unsupported by the selected model/adapter.", "success": False}
    items = [{"type": "inputText", "text": reply.content}]
    for image in reply.images:
        if image.media_type not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
            raise ValueError("unsupported inline image media type")
        encoded = base64.b64encode(image.data).decode("ascii")
        items.append({"type": "inputImage", "imageUrl": f"data:{image.media_type};base64,{encoded}"})
    result = {"content": reply.content, "success": reply.success}
    if reply.images:
        result["contentItems"] = items
    return result


class CodexModelSession(ModelSession):
    """A single model thread using experimental dynamic tools from Codex CLI."""

    context_capabilities = CODEX_CONTEXT_CAPABILITIES

    def __init__(self, model: str, effort: str | None = None, *, executable: str | None = None,
                 timeout: float | None = None) -> None:
        self.model = model
        self.effort = effort
        self.timeout = timeout
        self._temp = tempfile.TemporaryDirectory(prefix="ctfbot-codex-session-")
        self._executable = executable or find_codex_cli()
        self._server = CodexAppServer(self._executable, experimental_api=True)
        self._thread_id: str | None = None
        self._tool_schema_hash: str | None = None
        self._closed = False
        self._cancelled = threading.Event()
        self.turns_started = 0
        self._event_handler: Callable[[dict], None] | None = None
        self._message_handler: Callable[[str], None] | None = None
        self._context_checkpoint: Callable[[], None] | None = None
        self.supports_images = False
        try:
            self._server.start()
            account = self._server.account()
            if account.get("type") != "chatgpt":
                raise CodexAppServerError("The Codex CLI is not authenticated with a ChatGPT account.")
            try:
                self.supports_images = model_supports_images(self._server.models(include_hidden=True), self.model)
            except CodexAppServerError:
                # Capability discovery failure does not disable text solving.
                self.supports_images = False
        except Exception:
            self.close()
            raise

    def set_event_handler(self, handler: Callable[[dict], None]) -> None:
        self._event_handler = handler

    def set_message_handler(self, handler: Callable[[str], None]) -> None:
        """Observe public agentMessage items, never private reasoning content."""
        self._message_handler = handler

    def set_context_checkpoint(self, checkpoint: Callable[[], None]) -> None:
        self._context_checkpoint = checkpoint

    def reset_context(self) -> None:
        """Replace the conversation using normal session startup, no compact API.

        Restart the owned transport to discard in-flight calls/notifications too.
        The controller's sandbox, evidence and tool registries remain alive.
        Cancellation is sticky, including a user stop racing with this reset.
        """
        if self._closed or self._cancelled.is_set():
            raise RuntimeError("cannot reset a closed or cancelled model session")
        self._server.interrupt()
        self._server.close()
        self._thread_id = None
        self._tool_schema_hash = None
        self._server = CodexAppServer(self._executable, experimental_api=True)
        if self._cancelled.is_set():
            raise RuntimeError("model session cancelled during context reset")
        self._server.start()
        if self._cancelled.is_set():
            self._server.interrupt()
            raise RuntimeError("model session cancelled during context reset")

    def run_turn(self, prompt: str, tools: Sequence[ToolSpec], on_tool_call: ToolHandler,
                 *, timeout: float | None) -> TurnResult:
        if self._closed:
            raise RuntimeError("Codex model session is closed")
        if self._cancelled.is_set():
            raise RuntimeError("Codex model session was cancelled")
        deadlines = [value for value in (timeout, self.timeout) if value is not None]
        turn_timeout = min(deadlines) if deadlines else None
        schemas = [tool.as_provider_schema() for tool in tools]
        schema_hash = hashlib.sha256(json.dumps(schemas, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if self._thread_id is None:
            self._thread_id = self._server.start_dynamic_thread(
                self.model,
                self._temp.name,
                schemas,
                timeout=min(30, turn_timeout) if turn_timeout is not None else 30,
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
            return encode_tool_reply(reply, supports_images=getattr(self, "supports_images", False))

        event_handler = getattr(self, "_event_handler", None)
        def observe_event(event: dict) -> None:
            if event_handler is not None:
                event_handler(event)
            checkpoint = getattr(self, "_context_checkpoint", None)
            if checkpoint is not None:
                checkpoint()

        event_options = {"on_event": observe_event} if event_handler else {}
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
                timeout=turn_timeout,
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
