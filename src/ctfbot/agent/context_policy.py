"""Window pressure policy, independent of providers, tokenizers and transport.

Billing totals never describe occupancy. Prefer a reported occupancy, then the
latest inference's input + output, then a conservative UTF-8 byte estimate.
Unknown capacity disables the threshold until a caller supplies a window size.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any


def nonnegative_int(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


@dataclass(frozen=True, slots=True)
class ContextPolicy:
    capacity: int | None = None
    threshold: float = 0.8

    def __post_init__(self) -> None:
        if self.capacity is not None and (type(self.capacity) is not int or self.capacity <= 0):
            raise ValueError("context capacity must be a positive integer")
        if not 0 < self.threshold < 1:
            raise ValueError("context threshold must be between 0 and 1")


class ContextPressure:
    """Thread-safe measurements; the controller alone performs compaction.

    Local estimates count one token per UTF-8 byte to avoid language-specific
    assumptions. They are heuristics, not tokenizer-exact counts. A normalized
    measurement replaces the estimate; subsequent visible text adds to it.
    """

    def __init__(self, policy: ContextPolicy = ContextPolicy()) -> None:
        self.policy = policy
        self.capacity = policy.capacity
        self.used = 0
        self.basis = "utf8_estimate"
        self.generation = 0
        self._armed = True
        self._lock = threading.RLock()

    def observe(self, event: dict) -> None:
        if event.get("type") != "context_usage":
            return
        with self._lock:
            capacity = nonnegative_int(event.get("capacity"))
            if capacity and self.policy.capacity is None:
                self.capacity = capacity
            used = nonnegative_int(event.get("current_context_tokens"))
            basis = "reported"
            if used is None:
                last = event.get("last_inference_usage")
                if isinstance(last, dict):
                    inputs = nonnegative_int(last.get("inputTokens", last.get("input_tokens")))
                    outputs = nonnegative_int(last.get("outputTokens", last.get("output_tokens")))
                    used = inputs + (outputs or 0) if inputs is not None else nonnegative_int(last.get("totalTokens", last.get("total_tokens")))
                basis = "last_inference_estimate"
            if used is not None:
                self.used, self.basis = used, basis
                self._rearm_if_below_threshold()

    def add_text(self, text: str) -> None:
        with self._lock:
            self.used += len(text.encode("utf-8"))
            if self.basis == "reported":
                self.basis = "reported_plus_utf8_estimate"

    def _rearm_if_below_threshold(self) -> None:
        if self.capacity and self.used <= self.capacity * self.policy.threshold:
            self._armed = True

    @property
    def required(self) -> bool:
        with self._lock:
            return bool(self._armed and self.capacity and self.used > self.capacity * self.policy.threshold)

    def reset(self, seed: str) -> None:
        with self._lock:
            self.generation += 1
            self.used = len(seed.encode("utf-8"))
            self.basis = "utf8_estimate"
            # A seed larger than the threshold cannot shrink by resetting again.
            # Wait for a measurement below it, instead of looping without work.
            self._armed = False
            self._rearm_if_below_threshold()

    def describe(self) -> dict:
        with self._lock:
            return {"used": self.used, "capacity": self.capacity, "basis": self.basis,
                    "threshold": self.policy.threshold, "generation": self.generation}
