"""Keeps the team knowledge memory up to date (see knowledge/__init__.py).

Fed by the knowledge-index SQS queue (knowledge/events.py) and run once a day by an
EventBridge schedule with {"sweep": true}. It is separate from the answer path: nothing
here can delay or change a reply, and a failed job is retried, then parked in the DLQ.

A CHECK re-reads the whole thread from Slack and, unless a newer message will do it,
asks the model for a summary (knowledge/summary.py) and overwrites the thread's vectors:
one per problem the thread worked on and one per learning raised along the way, each
embedded from its own text and carrying the whole summary for the agent to cite.
It only ever reads the Slack thread: tool output from people's own accounts is never in
it, beyond what the bot chose to post.

The sweep catches what events can miss: for every channel with vectors, it deletes them
if the bot has left, the channel became Slack Connect or was excluded, and rewrites their
visibility tag if the channel switched between public and private.
"""

import json
import logging
import time
from collections import defaultdict
from decimal import Decimal

from slack_app.config import knowledge_excluded_channels
from slack_app.engaged_threads import thread_key
from slack_app.knowledge import (
    CHECK,
    DELETE_CHANNEL,
    DELETE_THREAD,
    SCHEMA_VERSION,
    channel_of,
    learning_key,
    problem_key,
    vector_keys,
)
from slack_app.knowledge.channels import ChannelUnknown, channel_info
from slack_app.knowledge.embeddings import embed
from slack_app.knowledge.store import knowledge_store
from slack_app.knowledge.summary import SummaryError, line, summarise
from slack_app.slack import bot_user_id, slack_client
from slack_app.thread_history import read_whole_thread

logger = logging.getLogger(__name__)

# What the summary model is given at most (about 50,000 tokens). A longer thread is
# summarised from its stored summary plus the messages since, or, when that isn't
# possible, its first message and as many of the latest as fit. Kept generous: the fix
# in a long incident thread is often in the middle.
MAX_PROMPT_CHARS = 200_000
MAX_PARTICIPANTS = 20

_PERSON_SUBTYPES = {None, "file_share", "thread_broadcast", "me_message"}


def handler(event: dict, context) -> dict:
    if event.get("sweep"):
        sweep()
        return {"ok": True}
    failures = []
    for record in event.get("Records", []):
        try:
            process(json.loads(record["body"]))
        except Exception:
            logger.exception("Knowledge job failed; SQS will retry it")
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}


def process(job: dict) -> str:
    """Runs one job and says what happened (for logs and tests)."""
    kind = job.get("kind")
    if kind == DELETE_CHANNEL:
        removed = knowledge_store().delete_channel(job["team_id"], job["channel"])
        outcome = f"deleted {removed} vectors"
    elif kind == DELETE_THREAD:
        removed = knowledge_store().delete_thread(thread_key(job["team_id"], job["channel"], job["thread_ts"]))
        outcome = f"deleted {removed} vectors"
    elif kind == CHECK:
        outcome = check(job)
    else:
        outcome = "unknown job"
    logger.info("Knowledge %s for %s: %s", kind, job.get("thread_ts") or job.get("channel"), outcome)
    return outcome


def check(job: dict) -> str:
    if job["channel"] in knowledge_excluded_channels():
        return "channel excluded"
    slack = slack_client()
    try:
        channel = channel_info(slack, job["channel"])
    except ChannelUnknown:
        return "channel unknown, skipped"  # fail closed: maybe a Slack Connect channel
    if not channel.indexable:
        return "channel not indexed"

    bot = bot_user_id({})
    force = bool(job.get("force"))
    key = thread_key(job["team_id"], channel.id, job["thread_ts"])
    messages = read_whole_thread(slack, channel.id, job["thread_ts"], bot)
    store = knowledge_store()

    if not messages or messages[0][0].get("ts") != job["thread_ts"]:
        return f"first message gone, deleted {store.delete_thread(key)} vectors"
    if len(messages) < 2:
        # Not a thread (yet). If it used to be one, its replies were deleted.
        return f"no replies, deleted {store.delete_thread(key)} vectors" if force else "no replies"

    trigger = Decimal(job["trigger_ts"])
    if not force and any(_by_a_person(raw, bot) and _last_activity(raw) > trigger for raw, _ in messages):
        return "superseded by a newer message"

    existing = store.get(vector_keys(key))
    # Every vector of a thread carries the same summary and last_message_ts.
    previous = next((vector["metadata"] for vector in existing.values() if vector.get("metadata")), {})
    last_ts = messages[-1][0]["ts"]
    if (
        not force
        and previous.get("schema_version") == SCHEMA_VERSION
        and Decimal(previous.get("last_message_ts") or "0") >= Decimal(last_ts)
    ):
        return "already up to date"

    lines, previous_summary, omitted = _prompt_lines(messages, previous, force)
    try:
        summary = summarise(lines, previous_summary, omitted)
    except SummaryError as err:
        return f"summary not usable ({err}), left as it was"
    if summary is None:
        if existing:
            store.delete(list(existing))
        return f"SKIP, deleted {len(existing)} vectors"

    metadata = {
        "team_id": job["team_id"],
        "channel_id": channel.id,
        "thread_key": key,
        "visibility": channel.visibility,
        "updated_at": int(time.time()),
        "status": summary.status,
        "kind": summary.kind,
        "schema_version": SCHEMA_VERSION,
        "summary": summary.text,
        "permalink": _permalink(slack, channel.id, job["thread_ts"]),
        "channel_name": channel.name,
        "participants": _participants(messages, bot),
        "last_message_ts": last_ts,
    }
    # Leave out empty values (a channel name Slack didn't give, no people left in a thread).
    metadata = {name: value for name, value in metadata.items() if value not in ("", [], None)}
    # A problem's vector carries that problem's status, so a search that matches the open
    # second problem of a thread doesn't report it as resolved.
    vectors = [
        {"key": problem_key(key, n), "embedding": embed(text), "metadata": {**metadata, "status": problem.status}}
        for n, (problem, text) in enumerate(zip(summary.problems, summary.problem_texts(), strict=True), 1)
    ] + [
        {"key": learning_key(key, n), "embedding": embed(text), "metadata": metadata}
        for n, text in enumerate(summary.learning_texts(), 1)
    ]
    store.put(vectors)
    stale = sorted(set(existing) - {vector["key"] for vector in vectors})
    if stale:
        store.delete(stale)
    return f"stored {len(vectors)} vectors"


def _prompt_lines(messages: list, previous: dict, force: bool) -> tuple[list[str], str | None, int]:
    """(transcript lines, the stored summary they continue or None, messages left out)."""
    lines = [line(raw, message) for raw, message in messages]
    if _size(lines) <= MAX_PROMPT_CHARS:
        return lines, None, 0
    previous_summary = None
    # An edit or a delete may have changed anything, so it always starts from the thread.
    if not force and previous.get("schema_version") == SCHEMA_VERSION and previous.get("summary"):
        since = Decimal(previous.get("last_message_ts") or "0")
        lines = [text for (raw, _), text in zip(messages, lines, strict=True) if Decimal(raw["ts"]) > since]
        previous_summary = previous["summary"]
    budget = MAX_PROMPT_CHARS - len(previous_summary or "")
    if not lines or _size(lines) <= budget:
        return lines, previous_summary, 0
    tail: list[str] = []
    used = len(lines[0])
    for text in reversed(lines[1:]):
        if used + len(text) + 1 > budget:
            break
        tail.insert(0, text)
        used += len(text) + 1
    return [lines[0], *tail], previous_summary, len(lines) - 1 - len(tail)


def _size(lines: list[str]) -> int:
    return sum(len(text) + 1 for text in lines)


def _by_a_person(raw: dict, bot: str | None) -> bool:
    return (
        bool(raw.get("user"))
        and not raw.get("bot_id")
        and raw.get("user") != bot
        and raw.get("subtype") in _PERSON_SUBTYPES
    )


def _last_activity(raw: dict) -> Decimal:
    edited = (raw.get("edited") or {}).get("ts")
    return max(Decimal(raw.get("ts") or "0"), Decimal(edited or "0"))


def _participants(messages: list, bot: str | None) -> list[str]:
    names: list[str] = []
    for raw, message in messages:
        if _by_a_person(raw, bot) and message.author not in names:
            names.append(message.author)
    return names[:MAX_PARTICIPANTS]


def _permalink(slack, channel: str, ts: str) -> str:
    try:
        return slack.chat_getPermalink(channel=channel, message_ts=ts)["permalink"]
    except Exception:
        logger.info("chat.getPermalink failed for %s; using the archive link", ts, exc_info=True)
        return f"https://slack.com/archives/{channel}/p{ts.replace('.', '')}"


def sweep() -> dict:
    """Once a day: drop channels the bot may no longer index, fix visibility tags. Returns counts."""
    store = knowledge_store()
    slack = slack_client()
    excluded = knowledge_excluded_channels()
    by_channel: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for vector in store.list(metadata=True):
        by_channel[channel_of(vector["key"])].append(vector)

    counts = {"channels": len(by_channel), "deleted": 0, "retagged": 0, "unknown": 0}
    for (_, channel_id), vectors in by_channel.items():
        try:
            channel = channel_info(slack, channel_id)
        except ChannelUnknown:
            # Not deleted on a hunch: a Slack outage mustn't wipe the team's memory. The
            # agent's search scope fails closed on its own (knowledge/scope.py).
            logger.warning("Sweep: couldn't check channel %s; trying again tomorrow", channel_id)
            counts["unknown"] += 1
            continue
        keys = [vector["key"] for vector in vectors]
        if not channel.indexable or channel_id in excluded:
            store.delete(keys)
            counts["deleted"] += len(keys)
            continue
        retag = [vector["key"] for vector in vectors if (vector.get("metadata") or {}).get("visibility") != channel.visibility]
        if retag:
            full = store.get(retag, data=True)
            store.put(
                [
                    {
                        "key": vector_key,
                        "embedding": vector["data"]["float32"],
                        "metadata": {**vector["metadata"], "visibility": channel.visibility},
                    }
                    for vector_key, vector in full.items()
                ]
            )
            counts["retagged"] += len(full)
    logger.info("Knowledge sweep: %s", counts)
    return counts
