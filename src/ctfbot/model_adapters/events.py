"""Codex schema event normalization, without guessing current context occupancy."""
from __future__ import annotations

from datetime import datetime, timezone

# Confirmed from codex-cli 0.159.0-alpha.3 generate-json-schema --experimental.
CODEX_CONTEXT_CAPABILITIES = {
    "token_usage_event": "thread/tokenUsage/updated",
    "compaction_item": "contextCompaction",
    "legacy_compaction_event": "thread/compacted",
    "current_context_tokens": False,
    "automatic_restore": "next tool reply or continuation; no asynchronous prompt",
}


def usage_numbers(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    return {key: value[key] for key in ("inputTokens", "outputTokens", "totalTokens", "cachedInputTokens", "cacheWriteInputTokens", "reasoningOutputTokens")
            if type(value.get(key)) is int and value[key] >= 0}


def normalize_event(message: dict, thread_id: str, turn_id: str, sequence: int) -> dict | None:
    params = message.get('params', {})
    if not isinstance(params, dict):
        return None
    if params.get('threadId') != thread_id or params.get('turnId') != turn_id:
        return None
    method = message.get('method')
    item = params.get('item', {})
    if not isinstance(item, dict):
        item = {}
    event = {"thread_id": thread_id, "turn_id": turn_id, "sequence": sequence,
             "timestamp_utc": datetime.now(timezone.utc).isoformat(), "source": "codex_app_server"}
    if method == 'thread/tokenUsage/updated':
        usage = params.get('tokenUsage', {})
        if not isinstance(usage, dict):
            return None
        # `last` is latest inference usage, not an authoritative occupied-window
        # counter. `total` is cumulative billing; never sum repeated snapshots.
        return {**event, "type": "context_usage", "current_context_tokens": None,
                "capacity": usage.get('modelContextWindow') if type(usage.get('modelContextWindow')) is int and usage['modelContextWindow'] > 0 else None, "capacity_source": method,
                "last_inference_usage": usage_numbers(usage.get('last')), "cumulative_usage": usage_numbers(usage.get('total'))}
    if method in {'item/started', 'item/completed'} and item.get('type') == 'contextCompaction':
        if not isinstance(item.get('id'), str) or not item['id']:
            return None
        return {**event, "type": 'compaction_started' if method == 'item/started' else 'compaction_completed',
                "epoch": item.get('id'), "legacy": False}
    if method == 'thread/compacted':
        return {**event, "type": "compaction_completed", "epoch": None, "legacy": True}
    if method == 'error':
        return {**event, "type": "provider_error", "error_code": params.get('error', {}).get('codexErrorInfo'),
                "will_retry": params.get('willRetry')}
    return None
