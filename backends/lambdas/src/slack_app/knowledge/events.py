"""Which Slack events queue work for the knowledge indexer (called by slack_events).

The rule: summarise a thread once it has been quiet for QUIET_SECONDS, and again whenever
it's picked up later. Each message a person posts queues a delayed CHECK. When the check
runs, the indexer skips it if a newer message exists, because that message's own check
covers it. A burst of messages therefore produces a single summary call.

  message from a person      -> CHECK after QUIET_SECONDS (the indexer skips threads without replies)
  edit (message_changed)     -> CHECK after QUIET_SECONDS, re-summarising the whole thread
  reply deleted              -> the same
  first message deleted      -> DELETE_THREAD now (Slack sends a "tombstone" edit if it had replies)
  bot removed from a channel -> DELETE_CHANNEL now (channel_left for public, group_left for private)

Bot messages, including this bot's, never queue a check: the next person's message does,
and the summary includes them. Nothing here reads Slack or calls a model, to stay inside
slack_events' 3-second budget: one SQS message per event, at most, and no message content.
"""

import logging
import os

from slack_app.config import knowledge_excluded_channels
from slack_app.knowledge import CHECK, DELETE_CHANNEL, DELETE_THREAD
from slack_app.work_queue import enqueue_knowledge

logger = logging.getLogger(__name__)

QUIET_SECONDS = 600
_PERSON_SUBTYPES = {None, "file_share", "thread_broadcast", "me_message"}


def quiet_seconds() -> int:
    # SQS delays go up to 15 minutes. Lower it locally to try the indexer without waiting.
    return max(0, min(int(os.getenv("KNOWLEDGE_QUIET_SECONDS", QUIET_SECONDS)), 900))


def note(payload: dict, event: dict) -> None:
    """Queues indexer work for this event, if there is any. Never raises: answering comes first."""
    try:
        job = job_for(payload, event)
        if job:
            delay = quiet_seconds() if job["kind"] == CHECK else 0
            enqueue_knowledge(job, delay)
            logger.info("Queued knowledge %s for %s in %ss", job["kind"], job.get("thread_ts") or job["channel"], delay)
    except Exception:
        logger.warning("Failed to queue knowledge work; answering is unaffected", exc_info=True)


def job_for(payload: dict, event: dict) -> dict | None:
    team_id = payload.get("team_id") or event.get("team") or ""
    channel = event.get("channel") or ""
    if not team_id or not channel or channel in knowledge_excluded_channels():
        return None
    base = {"team_id": team_id, "channel": channel}

    if event.get("type") in ("channel_left", "group_left"):
        return {**base, "kind": DELETE_CHANNEL}
    if event.get("type") != "message" or event.get("channel_type") not in ("channel", "group"):
        return None  # DMs and group DMs are never indexed
    if payload.get("is_ext_shared_channel"):
        return None  # Slack Connect; the indexer checks again with conversations.info

    subtype = event.get("subtype")
    if subtype == "message_changed":
        return _edit(base, event)
    if subtype == "message_deleted":
        return _deletion(base, event)
    if subtype in _PERSON_SUBTYPES and _from_a_person(event):
        thread_ts = event.get("thread_ts") or event["ts"]
        return {**base, "kind": CHECK, "thread_ts": thread_ts, "trigger_ts": event["ts"], "force": False}
    return None


def _edit(base: dict, event: dict) -> dict | None:
    message = event.get("message") or {}
    previous = event.get("previous_message") or {}
    thread_ts = message.get("thread_ts")
    if message.get("subtype") == "tombstone":
        # A first message deleted while it has replies stays as "This message was deleted."
        return {**base, "kind": DELETE_THREAD, "thread_ts": thread_ts or message.get("ts")}
    if not thread_ts or not _from_a_person(message):
        return None  # no replies yet, or the bot updating its own placeholder
    if message.get("text") == previous.get("text") and message.get("files") == previous.get("files"):
        return None  # a link unfurl or a reply count changing, not an edit
    return {**base, "kind": CHECK, "thread_ts": thread_ts, "trigger_ts": _event_ts(event), "force": True}


def _deletion(base: dict, event: dict) -> dict | None:
    previous = event.get("previous_message") or {}
    thread_ts = previous.get("thread_ts")
    if not thread_ts:
        return None
    if event.get("deleted_ts") == thread_ts:
        return {**base, "kind": DELETE_THREAD, "thread_ts": thread_ts}
    return {**base, "kind": CHECK, "thread_ts": thread_ts, "trigger_ts": _event_ts(event), "force": True}


def _from_a_person(message: dict) -> bool:
    return bool(message.get("user")) and not message.get("bot_id")


def _event_ts(event: dict) -> str:
    return event.get("event_ts") or event.get("ts") or ""
