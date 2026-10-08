"""Small credential scrubber for error summaries written to run traces."""

from __future__ import annotations

import re


def redact_sensitive_text(value: str, *, limit: int = 500) -> str:
    value = re.sub(r"(?i)(bearer\s+)\S+", r"\1[redacted]", value)
    value = re.sub(
        r"(?i)([\"']?(?:access_token|refresh_token|api[_-]?key|password|secret)[\"']?\s*[:=]\s*[\"']?)[^\"'\s,}&]+",
        r"\1[redacted]",
        value,
    )
    value = re.sub(r"\beyJ[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{8,}(?:\.[A-Za-z0-9_-]+)?", "[redacted-token]", value)
    return value[:limit]
