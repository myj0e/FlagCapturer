"""A compact display of observed usage, never cumulative billing as occupancy."""
from __future__ import annotations

from typing import Any

from ctfbot.tui.safe_text import safe_plain_text


def token_count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def format_tokens(value: int | None) -> str:
    if value is None:
        return "—"
    if value < 1000:
        return str(value)
    if round(value / 1000, 1) >= 1000:
        return f"{value / 1_000_000:.1f}".removesuffix(".0") + "M"
    return f"{value / 1000:.1f}".removesuffix(".0") + "K"


class ContextStatus:
    def __init__(self) -> None:
        self.provider = ""
        self.model = ""
        self.capacity: int | None = None
        self.used: int | None = None
        self.estimated = False
        self.compacting = False
        self.total_tokens: int | None = None
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None
        self._provider_totals = False

    def update_usage(self, usage: Any, *, provider: bool = False) -> None:
        if not isinstance(usage, dict) or (self._provider_totals and not provider):
            return
        def count(*names: str) -> int | None:
            return next((value for name in names if (value := token_count(usage.get(name))) is not None), None)
        inputs = count("inputTokens", "input_tokens", "prompt_tokens")
        outputs = count("outputTokens", "output_tokens", "completion_tokens")
        total = count("totalTokens", "total_tokens")
        if total is None and inputs is not None and outputs is not None:
            total = inputs + outputs
        if inputs is None and outputs is None and total is None:
            return
        self._provider_totals = provider
        # Cumulative snapshots may repeat or lag; never make usage go backwards.
        for field, value in (("input_tokens", inputs), ("output_tokens", outputs), ("total_tokens", total)):
            if value is not None:
                previous = getattr(self, field)
                setattr(self, field, max(previous or 0, value))

    def update(self, event: dict[str, Any]) -> None:
        self.update_usage(event.get("billing_totals") or event.get("cumulative_usage"), provider=True)
        capacity = token_count(event.get("capacity"))
        if capacity:
            self.capacity = capacity
        self.used = token_count(event.get("current_context_tokens", event.get("usage")))
        self.estimated = bool(event.get("estimated", False))
        self.compacting = False
        if self.used is None:
            last = event.get("last_inference_usage")
            if isinstance(last, dict):
                self.used = token_count(last.get("totalTokens"))
                if self.used is None:
                    inputs = token_count(last.get("inputTokens"))
                    outputs = token_count(last.get("outputTokens"))
                    if inputs is not None:
                        self.used = inputs + (outputs or 0)
                self.estimated = self.used is not None

    def render(self) -> str:
        identity = " / ".join(safe_plain_text(value) for value in (self.provider, self.model) if value)
        suffix = f" · {identity}" if identity else ""
        usage = f"Token {format_tokens(self.total_tokens)}"
        if self.input_tokens is not None or self.output_tokens is not None:
            usage += f"（入 {format_tokens(self.input_tokens)} / 出 {format_tokens(self.output_tokens)}）"
        prefix = usage + " · "
        if self.compacting:
            return prefix + f"上下文压缩中 · 窗口 {format_tokens(self.capacity)}{suffix}"
        if self.used is None:
            return prefix + f"上下文等待用量 · 窗口 {format_tokens(self.capacity)}{suffix}"
        label = "上下文≈" if self.estimated else "上下文 "
        percent = f" ({self.used / self.capacity:.0%})" if self.capacity else ""
        basis = " · 最近推理" if self.estimated else ""
        return prefix + f"{label}{format_tokens(self.used)} / {format_tokens(self.capacity)}{percent}{basis}{suffix}"
