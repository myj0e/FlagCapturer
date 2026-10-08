"""Controller-only exact-string benchmark oracle verification."""

from __future__ import annotations

import hmac
import json
from pathlib import Path

from ctfbot.evidence.store import require_private_regular_file


def verify_exact(oracle_path: Path, candidate: str) -> bool:
    if len(candidate.encode("utf-8")) > 4096:
        return False
    private_path = require_private_regular_file(oracle_path)
    payload = json.loads(private_path.read_text(encoding="utf-8"))
    expected = payload.get("flag") if isinstance(payload, dict) else None
    if not isinstance(expected, str) or not expected:
        raise ValueError("oracle file does not contain an exact-string flag")
    return hmac.compare_digest(candidate.rstrip("\r\n"), expected)
