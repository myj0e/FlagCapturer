"""Serialize provider callbacks into controller state and restoration epochs."""
from __future__ import annotations

import threading
from collections import deque


class ContextEvents:
    def __init__(self, registry):
        self.registry = registry
        self._lock = threading.Lock()
        self._queue = deque()
        self._overflow = False
        self._seen = set()
        self.pending: str | None = None
        self._cumulative = {}
        self.billing_totals = {}

    def enqueue(self, event: dict) -> None:
        with self._lock:
            if event.get('type') == 'context_usage':
                self._queue = deque(e for e in self._queue if not (
                    e.get('type') == 'context_usage' and e.get('thread_id') == event.get('thread_id')
                    and e.get('turn_id') == event.get('turn_id')))
            if len(self._queue) >= 128:
                self._overflow = True
            else:
                self._queue.append(dict(event))

    def drain(self) -> None:
        with self._lock:
            if self._overflow:
                raise RuntimeError('provider context event queue overflow')
            events = list(self._queue)
            self._queue.clear()
        for event in events:
            kind = event.get('type')
            if kind == 'context_usage':
                self.registry.provider_context = {
                    "usage": None, "capacity": event.get('capacity'), "source": event['source'],
                    "last_inference_usage": event.get('last_inference_usage'),
                    "observed_at": event['timestamp_utc'],
                }
                total = event.get('cumulative_usage')
                delta = {}
                if isinstance(total, dict):
                    thread = event['thread_id']
                    previous = self._cumulative.get(thread, {})
                    for key, value in total.items():
                        if type(value) is int and value >= 0:
                            delta[key] = max(0, value - previous.get(key, 0))
                            self.billing_totals[key] = self.billing_totals.get(key, 0) + delta[key]
                    self._cumulative[thread] = {key: max(previous.get(key, 0), value)
                                                for key, value in total.items() if type(value) is int and value >= 0}
                self.registry.evidence.append('provider_context_usage', **event, billing_delta=delta)
            elif kind in {'compaction_started', 'compaction_completed'}:
                epoch = event.get('epoch') or f"legacy:{event['thread_id']}:{event['turn_id']}"
                identity = (event['thread_id'], event['turn_id'], epoch, kind)
                # Legacy notification can accompany the same compaction item.
                if event.get('legacy') and any(k[:2] == identity[:2] and k[3] == kind for k in self._seen):
                    continue
                legacy_identity = (identity[0], identity[1], f"legacy:{identity[0]}:{identity[1]}", kind)
                if (not event.get('legacy') and legacy_identity in self._seen and
                        not any(k[:2] == identity[:2] and not str(k[2]).startswith('legacy:') and k[3] == kind for k in self._seen)):
                    self._seen.add(identity)
                    if self.pending == legacy_identity[2]:
                        self.pending = str(epoch)
                    continue
                if identity in self._seen:
                    continue
                if len(self._seen) >= 4096:
                    raise RuntimeError('provider context event identity limit exceeded')
                self._seen.add(identity)
                if kind == 'compaction_completed':
                    self.pending = str(epoch)
                self.registry.evidence.append('provider_' + kind, **event, restore_pending=self.pending)
            elif kind == 'provider_error':
                self.registry.evidence.append('provider_adapter_error', **event)

    def delivered(self, *, channel: str, revision: int, artifact: dict) -> None:
        if self.pending is None:
            return
        self.registry.evidence.append('context_restore_delivered', epoch=self.pending,
                                      channel=channel, revision=revision, snapshot=artifact)
        self.pending = None
