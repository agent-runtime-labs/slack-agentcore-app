"""Short-lived records that bind an OAuth consent session to the Slack user who started it.

Flow: the worker stores a record under a random nonce and DMs the user a link to
/oauth2/start?nonce=...; /oauth2/start drops the nonce into a cookie and redirects
to LinkedIn; /oauth2/callback reads the cookie back and checks the session URI
before completing the token exchange. The nonce is single-use.
"""

import os
import secrets
import threading
import time
from dataclasses import asdict, dataclass
from functools import lru_cache

import boto3

from slack_app.config import is_local

TTL_SECONDS = 600


@dataclass(frozen=True)
class PendingAuth:
    nonce: str
    runtime_user_id: str
    session_uri: str
    authorization_url: str
    channel: str
    slack_user: str
    thread_ts: str
    expires_at: int

    def expired(self, now: float | None = None) -> bool:
        return (now or time.time()) >= self.expires_at


def new_nonce() -> str:
    return secrets.token_urlsafe(32)


class DynamoPendingAuthStore:
    def __init__(self, table_name: str):
        self._table = boto3.resource("dynamodb").Table(table_name)

    def put(self, item: PendingAuth) -> None:
        self._table.put_item(Item=asdict(item))

    def get(self, nonce: str) -> PendingAuth | None:
        found = self._table.get_item(Key={"nonce": nonce}, ConsistentRead=True).get("Item")
        return _from_item(found) if found else None

    def consume(self, nonce: str) -> PendingAuth | None:
        """Delete and return the record; None if it was already used."""
        try:
            old = self._table.delete_item(
                Key={"nonce": nonce},
                ConditionExpression="attribute_exists(nonce)",
                ReturnValues="ALL_OLD",
            )
        except self._table.meta.client.exceptions.ConditionalCheckFailedException:
            return None
        return _from_item(old["Attributes"])


class MemoryPendingAuthStore:
    """Local-only store (single process). Records are lost when the server reloads."""

    def __init__(self):
        self._items: dict[str, PendingAuth] = {}
        self._lock = threading.Lock()

    def put(self, item: PendingAuth) -> None:
        with self._lock:
            self._items[item.nonce] = item

    def get(self, nonce: str) -> PendingAuth | None:
        with self._lock:
            return self._items.get(nonce)

    def consume(self, nonce: str) -> PendingAuth | None:
        with self._lock:
            return self._items.pop(nonce, None)


def _from_item(item: dict) -> PendingAuth:
    data = dict(item)
    data["expires_at"] = int(data["expires_at"])  # DynamoDB returns Decimal
    return PendingAuth(**data)


@lru_cache(maxsize=1)
def pending_auth_store() -> DynamoPendingAuthStore | MemoryPendingAuthStore:
    table = os.getenv("PENDING_AUTH_TABLE")
    if table:
        return DynamoPendingAuthStore(table)
    if not is_local():
        raise RuntimeError("PENDING_AUTH_TABLE is not set")
    return MemoryPendingAuthStore()
