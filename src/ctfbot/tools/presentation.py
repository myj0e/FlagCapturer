"""One byte-bounded JSON presentation for full, separately preserved tool data."""
from __future__ import annotations

import json
from typing import Any


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def text_preview(text: str, size: int) -> dict:
    raw = text.encode("utf-8")
    half = size // 2
    head = raw[:half].decode("utf-8", errors="ignore")
    tail = raw[-half:].decode("utf-8", errors="ignore") if half else ""
    return {"head": head, "tail": tail, "bytes": len(raw), "encoding": "utf8", "coordinate_space": "decoded_text_utf8_bytes",
            "omitted_byte_range": [len(head.encode()), len(raw) - len(tail.encode())]}


def render(payload: dict, maximum: int, *, artifact: dict | None = None) -> str:
    """Crop values rather than slicing serialized JSON; keep status and references.

    The caller owns the complete source artifact. Re-rendering at a lower budget
    uses that source, so previews never become nested JSON strings.
    """
    full = dumps(payload)
    if len(full.encode()) <= maximum:
        return full
    result: dict = {"truncated": True}
    for key in ("status", "error_kind", "execution_state", "exit_code", "timed_out", "session_id",
                "source_ref", "observation_id", "candidate_id", "claim_id", "experiment_id", "revision", "next_cursor", "output_warning"):
        if key in payload:
            trial = {**result, key: payload[key]}
            if len(dumps(trial).encode()) <= maximum:
                result = trial
    if artifact:
        result["raw_response_artifact"] = artifact["artifact"]
        if len(dumps(result).encode()) > maximum:
            result.pop("raw_response_artifact")
    for key in ("stdout_evidence", "stderr_evidence", "source", "observation", "run_budget"):
        if key not in payload:
            continue
        value = payload[key]
        if isinstance(value, dict) and "artifact" in value:
            value = value["artifact"]
        trial = {**result, key: value}
        if len(dumps(trial).encode()) <= maximum * .75:
            result = trial
    strings = {key: value for key, value in payload.items() if isinstance(value, str)
               and key not in result and key not in {"status", "error_kind", "execution_state", "session_id", "source_ref"}}
    # stdout and stderr have independent tails, even for a single huge line.
    strings = {key: strings[key] for key in ("stdout", "stderr", "output", "data", "text", "message") if key in strings}
    omitted = [key for key in payload if key not in result and key not in strings]
    if omitted:
        trial = {**result, "omitted_fields": omitted}
        if len(dumps(trial).encode()) <= maximum * .85:
            result = trial
    if not strings:
        # Nested JSON remains in the source artifact. Never present a sliced
        # serialization as if it were a usable record or structured response.
        return dumps(result)
    low, high = 0, maximum
    best = result
    while low <= high:
        size = (low + high) // 2
        trial = {**result, "previews": {key: text_preview(value, size // len(strings)) for key, value in strings.items()}}
        if len(dumps(trial).encode()) <= maximum:
            best = trial
            low = size + 1
        else:
            high = size - 1
    return dumps(best) if len(dumps(best).encode()) <= maximum else "{}"
