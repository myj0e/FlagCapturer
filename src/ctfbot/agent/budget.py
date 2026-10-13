"""Transmission accounting without cumulative quotas; bytes are not context tokens."""
from __future__ import annotations

from ctfbot.agent.context import RECOVERY_TOOLS


OBSERVATION_TOOLS = RECOVERY_TOOLS | {
    "session_read", "challenge_list", "challenge_read_text", "challenge_read_bytes",
    "experiment_record", "claim_record", "summary_update", "workflow_read", "image_view",
}


def tool_category(name: str) -> str:
    if name in {"run_complete", "session_close"}:
        return "control"
    return "observation" if name in OBSERVATION_TOOLS else "execution"


class TransmissionAccounting:
    """Record cumulative transmission sizes without enforcing a quota."""

    def __init__(self):
        self.used = dict.fromkeys(("ordinary", "recovery", "closing"), 0)
        self.tool_reply_bytes = 0
        self.prompt_restore_bytes = 0
        self.prompt_bytes = 0
        self.argument_bytes = 0
        self.public_message_bytes = 0
        self.image_bytes = 0
        self.image_count = 0

    def pool(self, tool: str) -> str:
        if tool in {"run_complete", "session_close"}:
            return "closing"
        return "recovery" if tool in RECOVERY_TOOLS else "ordinary"

    def charge_parts(self, pool: str, text: str, restore_bytes: int) -> None:
        size = len(text.encode("utf-8"))
        if pool == "recovery":
            self.used[pool] += size
        else:
            self.used[pool] += size - restore_bytes
            self.used["recovery"] += restore_bytes
        self.tool_reply_bytes += size

    def charge_prompt_restore(self, text: str) -> None:
        size = len(text.encode("utf-8"))
        self.used["recovery"] += size
        self.prompt_restore_bytes += size

    def telemetry(self) -> dict:
        return {"schema_version": 2, "semantics": "actual UTF-8 bytes sent; not context token occupancy; no cumulative quota",
                "tool_reply_bytes": self.tool_reply_bytes, "prompt_restore_bytes": self.prompt_restore_bytes,
                "pools": {key: {"used": value} for key, value in self.used.items()},
                "prompt_bytes": self.prompt_bytes, "argument_bytes": self.argument_bytes,
                "public_message_bytes": self.public_message_bytes,
                "image_bytes": self.image_bytes, "image_count": self.image_count}
