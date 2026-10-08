"""Provider-neutral model turn contracts used by the headless runner."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
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
class ToolReply:
    content: str
    success: bool = True


@dataclass(frozen=True, slots=True)
class TurnResult:
    text: str
    usage: Mapping[str, Any] | None = None
    response_id: str | None = None
    finish_reason: str | None = None
    tool_calls: int = 0


ToolHandler = Callable[[ToolCall], ToolReply]


class ModelSession(Protocol):
    """One conversation with a provider; the provider may call ctfbot tools."""

    def run_turn(
        self,
        prompt: str,
        tools: Sequence[ToolSpec],
        on_tool_call: ToolHandler,
        *,
        timeout: float,
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
        timeout: float,
    ) -> TurnResult:
        del prompt, timeout
        if self.closed:
            raise RuntimeError("fake model session is closed")
        if self._index >= len(self._turns):
            return TurnResult(text="", finish_reason="fake_transcript_exhausted")
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
