"""Threads the bot has replied in, so follow-ups there don't need an @mention.

Written whenever the bot posts its placeholder in a thread, read by slack_events for
every channel message that doesn't mention the bot. Entries expire after TTL_SECONDS
of silence from the bot; any new reply in the thread renews them.

Both operations are best-effort: a failed write only means the next follow-up goes
through triage instead, and a failed read is treated as "not engaged".
"""

import logging
import os
import threading
import time
from functools import lru_cache

import boto3

from slack_app.config import is_local

logger = logging.getLogger(__name__)

TTL_SECONDS = 7 * 24 * 3600


def thread_key(team_id: str, channel: str, thread_ts: str) -> str:
    return f"{team_id}:{channel}:{thread_ts}"


class DynamoEngagedThreadStore:
    def __init__(self, table_name: str):
        self._table = boto3.resource("dynamodb").Table(table_name)

    def add(self, key: str) -> None:
        self._table.put_item(Item={"thread_key": key, "expires_at": int(time.time()) + TTL_SECONDS})

    def contains(self, key: str) -> bool:
        item = self._table.get_item(Key={"thread_key": key}).get("Item")
        # DynamoDB deletes expired items lazily, so check the expiry ourselves.
        return bool(item) and int(item["expires_at"]) > time.time()


class MemoryEngagedThreadStore:
    """Local-only store (single process). Entries are lost when the server reloads."""

    def __init__(self):
        self._items: dict[str, float] = {}
        self._lock = threading.Lock()

    def add(self, key: str) -> None:
        with self._lock:
            self._items[key] = time.time() + TTL_SECONDS

    def contains(self, key: str) -> bool:
        with self._lock:
            return self._items.get(key, 0) > time.time()


@lru_cache(maxsize=1)
def engaged_thread_store() -> DynamoEngagedThreadStore | MemoryEngagedThreadStore:
    table = os.getenv("ENGAGED_THREADS_TABLE")
    if table:
        return DynamoEngagedThreadStore(table)
    if not is_local():
        raise RuntimeError("ENGAGED_THREADS_TABLE is not set")
    return MemoryEngagedThreadStore()


def mark_engaged(team_id: str, channel: str, thread_ts: str) -> None:
    try:
        engaged_thread_store().add(thread_key(team_id, channel, thread_ts))
    except Exception:
        logger.warning("Failed to remember thread %s", thread_ts, exc_info=True)


def is_engaged(team_id: str, channel: str, thread_ts: str) -> bool:
    try:
        return engaged_thread_store().contains(thread_key(team_id, channel, thread_ts))
    except Exception:
        logger.warning("Failed to look up thread %s", thread_ts, exc_info=True)
        return False
