"""Deletes everything kept for a person, when they turn memory off.

Runs in agent_worker, off the processing queue: the button click (slack_button_clicks)
only has 3 seconds, and has already recorded "declined", so nothing new is saved or read
while this runs.

For each of the person's tiers it deletes the raw events first (so nothing more is
extracted from them), then the records extracted so far. A failure raises, and SQS runs
the job again: every step is safe to repeat.

AgentCore extracts records in the background, so an event saved moments before the click
can still produce a record after this ran. Events also expire by themselves
(event_expiry_duration in memory.tf).
"""

import logging
from functools import lru_cache

import boto3

from slack_app.config import user_memory_id
from slack_app.user_memory import DM, PUBLIC, actor_id, namespaces
from slack_app.user_memory.consent import consent_store

logger = logging.getLogger(__name__)

_PAGE = 100


@lru_cache(maxsize=1)
def _memory():
    return boto3.client("bedrock-agentcore")


def forget(user_id: str) -> None:
    memory_id = user_memory_id()
    if not memory_id:
        logger.warning("USER_MEMORY_ID is not set; nothing to delete")
        return
    # Even if they have turned memory back on since: they asked for what was there to go.
    consent = consent_store().get(user_id)
    tiers = [DM, PUBLIC, *sorted(consent.tiers if consent else ())]
    events = records = 0
    for tier in tiers:
        actor = actor_id(user_id, tier)
        events += _delete_events(memory_id, actor)
        records += sum(_delete_records(memory_id, namespace) for namespace in namespaces(actor))
    consent_store().clear_tiers(user_id)
    logger.info("Deleted %d events and %d records across %d tiers", events, records, len(tiers))


def _delete_events(memory_id: str, actor: str) -> int:
    client = _memory()
    deleted = 0
    for session in _pages(client.list_sessions, "sessionSummaries", memoryId=memory_id, actorId=actor):
        session_id = session["sessionId"]
        listed = _pages(
            client.list_events, "events", memoryId=memory_id, actorId=actor, sessionId=session_id, includePayloads=False
        )
        for event in listed:
            client.delete_event(memoryId=memory_id, actorId=actor, sessionId=session_id, eventId=event["eventId"])
            deleted += 1
    return deleted


def _delete_records(memory_id: str, namespace: str) -> int:
    client = _memory()
    ids = [
        record["memoryRecordId"]
        for record in _pages(client.list_memory_records, "memoryRecordSummaries", memoryId=memory_id, namespace=namespace)
    ]
    for start in range(0, len(ids), _PAGE):
        batch = [{"memoryRecordId": record_id} for record_id in ids[start : start + _PAGE]]
        failed = client.batch_delete_memory_records(memoryId=memory_id, records=batch).get("failedRecords") or []
        if failed:
            raise RuntimeError(f"{len(failed)} memory records could not be deleted")
    return len(ids)


def _pages(call, key: str, **kwargs) -> list[dict]:
    """Every item, read in full before anything is deleted, so deleting can't disturb paging."""
    items: list[dict] = []
    token = None
    while True:
        response = call(maxResults=_PAGE, **kwargs, **({"nextToken": token} if token else {}))
        items += response.get(key) or []
        token = response.get("nextToken")
        if not token:
            return items
