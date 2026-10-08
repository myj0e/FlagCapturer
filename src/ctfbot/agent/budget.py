"""Transmission accounting; byte budgets are not provider context tokens."""
from __future__ import annotations

from ctfbot.agent.context import RECOVERY_TOOLS


OBSERVATION_TOOLS = RECOVERY_TOOLS | {
    "session_read", "challenge_list", "challenge_read_text", "challenge_read_bytes",
    "experiment_record", "claim_record", "summary_update", "workflow_read",
}


def tool_category(name: str) -> str:
    if name in {"run_complete", "session_close"}:
        return "control"
    return "observation" if name in OBSERVATION_TOOLS else "execution"


class ReplyBudget:
    def __init__(self, ordinary: int, recovery: int = 32768, closing: int = 8192):
        self.limits = {"ordinary": ordinary, "recovery": recovery, "closing": closing}
        self.used = dict.fromkeys(self.limits, 0)
        self.tool_reply_bytes = 0
        self.prompt_restore_bytes = 0
        self.prompt_bytes = 0
        self.argument_bytes = 0
        self.public_message_bytes = 0

    def pool(self, tool: str) -> str:
        if tool in {"run_complete", "session_close"}:
            return "closing"
        return "recovery" if tool in RECOVERY_TOOLS else "ordinary"

    def remaining(self, pool: str) -> int:
        return self.limits[pool] - self.used[pool]

    def allowance(self, pool: str, per_reply: int) -> int:
        if pool == "ordinary" and self.used[pool] >= self.limits[pool] * .8:
            per_reply = min(per_reply, 2048)
        return min(per_reply, self.remaining(pool))

    def charge(self, pool: str, text: str) -> None:
        size = len(text.encode("utf-8"))
        if size > self.remaining(pool):
            raise ValueError("reply exceeds its transmission budget")
        self.used[pool] += size
        self.tool_reply_bytes += size

    def charge_parts(self, pool: str, text: str, restore_bytes: int) -> None:
        size = len(text.encode("utf-8"))
        if pool == "recovery" or not restore_bytes:
            self.charge(pool, text)
            return
        base = size - restore_bytes
        if base > self.remaining(pool) or restore_bytes > self.remaining("recovery"):
            raise ValueError("reply and restoration exceed their transmission pools")
        self.used[pool] += base
        self.used["recovery"] += restore_bytes
        self.tool_reply_bytes += size

    def charge_prompt_restore(self, text: str) -> None:
        size = len(text.encode("utf-8"))
        if size > self.remaining("recovery"):
            raise ValueError("prompt restoration exceeds the recovery pool")
        self.used["recovery"] += size
        self.prompt_restore_bytes += size

    def telemetry(self) -> dict:
        return {"schema_version": 1, "semantics": "actual UTF-8 bytes sent; not context token occupancy",
                "tool_reply_bytes": self.tool_reply_bytes, "prompt_restore_bytes": self.prompt_restore_bytes, "pools": {
                    key: {"used": self.used[key], "limit": self.limits[key], "remaining": self.remaining(key)}
                    for key in self.used}, "ordinary_warning": self.used['ordinary'] >= self.limits['ordinary'] * .8,
                "prompt_bytes": self.prompt_bytes, "argument_bytes": self.argument_bytes,
                "public_message_bytes": self.public_message_bytes}
