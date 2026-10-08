"""Bounded transport queues that never silently evict RPCs or turn terminals."""
from __future__ import annotations

import json
import queue
import threading
import time
from collections import deque


def coalesce_key(message):
    if isinstance(message, dict) and message.get('method') == 'thread/tokenUsage/updated' and 'id' not in message:
        params = message.get('params', {})
        return message['method'], params.get('threadId'), params.get('turnId')
    return None


def expendable(message):
    if not isinstance(message, dict) or 'id' in message or '_ctfbot_parse_error' in message:
        return False
    return message.get('method') not in {
        'turn/completed', 'item/tool/call', 'item/started', 'item/completed',
        'item/agentMessage/delta', 'thread/compacted', 'error',
    }


class MessageBuffer(deque):
    def __init__(self, *, maximum=512, byte_limit=4 * 1024 * 1024):
        super().__init__()
        self.maximum, self.byte_limit = maximum, byte_limit

    def append(self, message):
        key = coalesce_key(message)
        if key:
            for index, old in enumerate(self):
                if coalesce_key(old) == key:
                    del self[index]
                    break
        size = len(json.dumps(message).encode())
        while len(self) >= self.maximum or sum(len(json.dumps(m).encode()) for m in self) + size > self.byte_limit:
            index = next((i for i, m in enumerate(self) if expendable(m)), None)
            if index is not None:
                del self[index]
            elif expendable(message):
                return
            else:
                raise OverflowError('Codex message queue cannot retain critical protocol messages')
        super().append(message)


class MessageQueue:
    def __init__(self, **limits):
        self.buffer = MessageBuffer(**limits)
        self.condition = threading.Condition()
        self.failed = False

    def put(self, message):
        with self.condition:
            try:
                self.buffer.append(message)
            except OverflowError:
                self.failed = True
            self.condition.notify()

    def get(self, timeout=None):
        deadline = time.monotonic() + timeout if timeout is not None else None
        with self.condition:
            while not self.buffer and not self.failed:
                remaining = deadline - time.monotonic() if deadline is not None else None
                if remaining is not None and remaining <= 0:
                    raise queue.Empty
                self.condition.wait(remaining)
            if self.failed:
                raise OverflowError('Codex message queue overflow; critical messages were not delivered')
            return self.buffer.popleft()


class PublicTextBuffer:
    """Bound final response assembly; full public messages use the event callback."""
    def __init__(self, maximum=65536):
        self.maximum = maximum
        self.text = ""
        self.truncated = False

    def append(self, text: str) -> None:
        self.truncated = self.truncated or len(self.text) + len(text) > self.maximum
        self.text = (self.text + text)[-self.maximum:]

    def result(self) -> str:
        return ("[Earlier public text omitted; see run evidence.]\n" if self.truncated else "") + self.text
