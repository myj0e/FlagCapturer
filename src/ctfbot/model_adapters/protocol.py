"""Provider-neutral model turn contracts used by the headless runner."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]

    def as_provider_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    arguments: Mapping[str, Any]
    call_id: str | None = None


@dataclass(frozen=True, slots=True)
class ToolImage:
    """Provider-neutral inline image. Binary payloads never enter text replies."""

    data: bytes = field(repr=False)
    media_type: str = "image/png"


@dataclass(frozen=True, slots=True)
class ToolReply:
    content: str
    success: bool = True
    images: tuple[ToolImage, ...] = ()


@dataclass(frozen=True, slots=True)
class TurnResult:
    text: str
    usage: Mapping[str, Any] | None = None
    response_id: str | None = None
    finish_reason: str | None = None
    tool_calls: int = 0


ToolHandler = Callable[[ToolCall], ToolReply]


class ContextCompactionRequired(Exception):
    """Cooperative yield at a context checkpoint, before executing another tool."""


class ResettableModelSession(Protocol):
    """Optional transport capability: discard conversation, retain tool runtime.

    Call reset_context only after run_turn returns or propagates
    ContextCompactionRequired. Adapters must propagate that signal unchanged.
    Checkpoints, if supported, run synchronously on the run_turn thread after
    usage notifications. They must never run in a background callback thread.
    """

    def reset_context(self) -> None: ...

    def set_context_checkpoint(self, checkpoint: Callable[[], None]) -> None: ...


class ModelSession(Protocol):
    """One conversation with a provider; the provider may call ctfbot tools."""

    # Optional capability: adapters without supports_images are text-only.
    # Image-capable adapters must transmit ToolReply.images as actual media.
    def run_turn(
        self,
        prompt: str,
        tools: Sequence[ToolSpec],
        on_tool_call: ToolHandler,
        *,
        timeout: float | None,
    ) -> TurnResult: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ScriptedTurn:
    tool_calls: tuple[ToolCall, ...] = ()
    text: str = ""
    usage: Mapping[str, Any] | None = None
    finish_reason: str = "completed"


class FakeModelSession:
    """Deterministic provider for synthetic/offline loop checks only."""

    def __init__(self, turns: Sequence[ScriptedTurn]) -> None:
        self._turns = tuple(turns)
        self._index = 0
        self.closed = False

    def run_turn(
        self,
        prompt: str,
        tools: Sequence[ToolSpec],
        on_tool_call: ToolHandler,
        *,
        timeout: float | None,
    ) -> TurnResult:
        del prompt, timeout
        if self.closed:
            raise RuntimeError("fake model session is closed")
        if self._index >= len(self._turns):
            raise RuntimeError("fake provider transcript exhausted without explicit completion")
        turn = self._turns[self._index]
        self._index += 1
        for call in turn.tool_calls:
            on_tool_call(call)
        return TurnResult(
            text=turn.text,
            usage=turn.usage,
            response_id=f"fake-{self._index}",
            finish_reason=turn.finish_reason,
            tool_calls=len(turn.tool_calls),
        )

    def close(self) -> None:
        self.closed = True


def normalize_usage(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Keep JSON-safe usage fields while excluding arbitrary provider objects."""
    if value is None:
        return None
    safe: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, (str, int, float, bool)) or item is None:
            safe[str(key)] = item
    return safe
