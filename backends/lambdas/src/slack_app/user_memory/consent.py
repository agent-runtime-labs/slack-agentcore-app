"""Whether a person has agreed to the bot remembering things about them.

One record per person (the runtime user ID, slack-<team>-<user>). No record, "asked" and
"declined" all mean no: nothing is saved and nothing is read. Only a button click by
that person (handlers/slack_button_clicks.py) ever writes "granted".

`notice_version` is the version of the notice they answered (prompt.py). Bump
NOTICE_VERSION when what the bot remembers changes: an older yes then no longer counts,
and the person is asked again.

`tiers` are the channel tiers this person has memory in (see __init__.py), so a DM can
recall them and turning memory off can delete them.
"""

import os
import threading
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from functools import lru_cache

import boto3

from slack_app.config import is_local

NOTICE_VERSION = 1

ASKED = "asked"
GRANTED = "granted"
DECLINED = "declined"


@dataclass(frozen=True)
class Consent:
    user_id: str
    status: str
    notice_version: int
    decided_at: str = ""
    tiers: frozenset[str] = frozenset()

    @property
    def granted(self) -> bool:
        return self.status == GRANTED and self.notice_version == NOTICE_VERSION

    @property
    def current(self) -> bool:
        """Whether this record answers (or at least was shown) the current notice."""
        return self.notice_version == NOTICE_VERSION


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class DynamoConsentStore:
    def __init__(self, table_name: str):
        self._table = boto3.resource("dynamodb").Table(table_name)

    def get(self, user_id: str) -> Consent | None:
        item = self._table.get_item(Key={"user_id": user_id}, ConsistentRead=True).get("Item")
        if not item:
            return None
        return Consent(
            user_id=user_id,
            status=str(item.get("status") or ""),
            notice_version=int(item.get("notice_version") or 0),
            decided_at=str(item.get("decided_at") or ""),
            tiers=frozenset(item.get("tiers") or ()),
        )

    def mark_asked(self, user_id: str) -> bool:
        """Note that the person was shown the current notice. False if they already were."""
        try:
            self._table.update_item(
                Key={"user_id": user_id},
                UpdateExpression="SET #status = :asked, notice_version = :version, decided_at = :now",
                ConditionExpression="attribute_not_exists(notice_version) OR notice_version < :version",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={":asked": ASKED, ":version": NOTICE_VERSION, ":now": _now()},
            )
        except self._table.meta.client.exceptions.ConditionalCheckFailedException:
            return False
        return True

    def decide(self, user_id: str, status: str) -> None:
        self._table.update_item(
            Key={"user_id": user_id},
            UpdateExpression="SET #status = :status, notice_version = :version, decided_at = :now",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":status": status, ":version": NOTICE_VERSION, ":now": _now()},
        )

    def add_tier(self, user_id: str, tier: str) -> None:
        self._table.update_item(
            Key={"user_id": user_id},
            UpdateExpression="ADD tiers :tier",
            ExpressionAttributeValues={":tier": {tier}},
        )

    def clear_tiers(self, user_id: str) -> None:
        self._table.update_item(Key={"user_id": user_id}, UpdateExpression="REMOVE tiers")


class MemoryConsentStore:
    """Local-only store (single process). Records are lost when the server reloads."""

    def __init__(self):
        self._items: dict[str, Consent] = {}
        self._lock = threading.Lock()

    def get(self, user_id: str) -> Consent | None:
        with self._lock:
            return self._items.get(user_id)

    def mark_asked(self, user_id: str) -> bool:
        with self._lock:
            found = self._items.get(user_id)
            if found and found.notice_version >= NOTICE_VERSION:
                return False
            tiers = found.tiers if found else frozenset()
            self._items[user_id] = Consent(user_id, ASKED, NOTICE_VERSION, _now(), tiers)
            return True

    def decide(self, user_id: str, status: str) -> None:
        with self._lock:
            found = self._items.get(user_id)
            tiers = found.tiers if found else frozenset()
            self._items[user_id] = Consent(user_id, status, NOTICE_VERSION, _now(), tiers)

    def add_tier(self, user_id: str, tier: str) -> None:
        with self._lock:
            found = self._items[user_id]
            self._items[user_id] = replace(found, tiers=found.tiers | {tier})

    def clear_tiers(self, user_id: str) -> None:
        with self._lock:
            found = self._items.get(user_id)
            if found:
                self._items[user_id] = replace(found, tiers=frozenset())


@lru_cache(maxsize=1)
def consent_store() -> DynamoConsentStore | MemoryConsentStore:
    table = os.getenv("MEMORY_CONSENT_TABLE")
    if table:
        return DynamoConsentStore(table)
    if not is_local():
        raise RuntimeError("MEMORY_CONSENT_TABLE is not set")
    return MemoryConsentStore()
