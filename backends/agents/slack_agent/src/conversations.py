"""In-process conversation history keyed by session ID.

AgentCore Runtime keeps one microVM per session (until it idles out), so a plain
dict gives multi-turn memory within a Slack thread without extra infrastructure.
Swap for AgentCore Memory if history must survive session timeouts.
"""

import copy
import threading
from collections import OrderedDict


class ConversationCache:
    def __init__(self, max_items: int):
        self._items: OrderedDict[str, list] = OrderedDict()
        self._max = max_items
        self._lock = threading.Lock()

    def get(self, session_id: str) -> list:
        with self._lock:
            messages = self._items.get(session_id)
            if messages is None:
                return []
            self._items.move_to_end(session_id)
            return copy.deepcopy(messages)

    def put(self, session_id: str, messages: list) -> None:
        with self._lock:
            self._items[session_id] = copy.deepcopy(messages)
            self._items.move_to_end(session_id)
            while len(self._items) > self._max:
                self._items.popitem(last=False)
