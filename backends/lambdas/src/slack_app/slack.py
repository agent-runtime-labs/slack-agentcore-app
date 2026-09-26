"""Slack Web API client and request signature verification."""

import logging
import re
import threading
import time
from decimal import Decimal
from functools import lru_cache

from slack_sdk import WebClient
from slack_sdk.signature import SignatureVerifier

from slack_app.config import slack_credentials, slack_dry_run
from slack_app.engaged_threads import mark_engaged

logger = logging.getLogger(__name__)

_MENTION = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]*)?>")

PLACEHOLDER_TEXT = "🤔 Thinking…"

# Reaction names (Slack emoji shortcodes, no colons) for the lifecycle of a request:
# added the moment we receive it, then swapped for one of the outcome reactions once
# agent_worker finishes. REACTION_DONE deliberately isn't a check mark: this is an AI
# reply in a channel with human collaborators, and a check mark reads as "verified
# correct" rather than "a reply was posted" -- speech_balloon makes no such claim.
REACTION_WORKING = "eyes"
REACTION_DONE = "speech_balloon"
REACTION_ERROR = "warning"
REACTION_AUTH_REQUIRED = "lock"
# A thank-you or "got it" aimed at the bot gets a thumbs up instead of a reply, as a
# person would do (see triage.REACT).
REACTION_ACK = "+1"

DRY_RUN_BOT_USER_ID = "UBOTLOCAL"


class DryRunSlackClient:
    """Stands in for WebClient when SLACK_DRY_RUN=true; logs instead of calling Slack.

    It also remembers the messages that pass through it (see record_incoming), so that
    conversations.replies returns a real thread and follow-ups can be tested locally.
    """

    def __init__(self):
        self._threads: dict[tuple[str, str], dict[str, dict]] = {}
        self._lock = threading.Lock()

    def _log(self, method: str, **kwargs) -> dict:
        logger.info("[slack dry-run] %s %s", method, kwargs)
        return {"ok": True, "ts": f"{time.time():.6f}"}

    def remember(self, channel: str, thread_ts: str, message: dict) -> None:
        with self._lock:
            self._threads.setdefault((channel, thread_ts), {})[message["ts"]] = message

    def chat_postMessage(self, **kwargs) -> dict:  # noqa: N802 - mirrors slack_sdk
        result = self._log("chat.postMessage", **kwargs)
        message = {"ts": result["ts"], "user": DRY_RUN_BOT_USER_ID, "text": kwargs.get("text", "")}
        self.remember(kwargs["channel"], kwargs.get("thread_ts") or result["ts"], message)
        return result

    def chat_update(self, **kwargs) -> dict:
        with self._lock:
            for (channel, _), messages in self._threads.items():
                if channel == kwargs["channel"] and kwargs["ts"] in messages:
                    messages[kwargs["ts"]]["text"] = kwargs.get("text", "")
        return self._log("chat.update", **kwargs)

    def conversations_replies(self, channel: str, ts: str, latest: str | None = None, **kwargs) -> dict:
        with self._lock:
            messages = list(self._threads.get((channel, ts), {}).values())
        if latest:
            messages = [message for message in messages if Decimal(message["ts"]) <= Decimal(latest)]
        return {"ok": True, "messages": sorted(messages, key=lambda message: Decimal(message["ts"]))}

    def chat_postEphemeral(self, **kwargs) -> dict:  # noqa: N802
        return self._log("chat.postEphemeral", **kwargs)

    def reactions_add(self, **kwargs) -> dict:
        return self._log("reactions.add", **kwargs)

    def reactions_remove(self, **kwargs) -> dict:
        return self._log("reactions.remove", **kwargs)

    def auth_test(self, **kwargs) -> dict:
        self._log("auth.test", **kwargs)
        return {"ok": True, "user_id": DRY_RUN_BOT_USER_ID}


@lru_cache(maxsize=1)
def slack_client() -> WebClient | DryRunSlackClient:
    if slack_dry_run():
        return DryRunSlackClient()
    return WebClient(token=slack_credentials().bot_token)


def record_incoming(event: dict, thread_ts: str) -> None:
    """Dry run only: note a person's message so the fake conversations.replies can return it."""
    client = slack_client()
    if isinstance(client, DryRunSlackClient):
        message = {"ts": event["ts"], "user": event.get("user"), "text": event.get("text", "")}
        if event.get("files"):
            message["files"] = event["files"]
        client.remember(event["channel"], thread_ts, message)


def add_reaction(client, channel: str, timestamp: str, name: str) -> None:
    """Best-effort: a missing reactions:write scope or any API error is logged, not raised."""
    try:
        client.reactions_add(channel=channel, timestamp=timestamp, name=name)
    except Exception:
        logger.warning("Failed to add :%s: reaction", name, exc_info=True)


def remove_reaction(client, channel: str, timestamp: str, name: str) -> None:
    """Best-effort, same as add_reaction -- also covers the reaction already being gone."""
    try:
        client.reactions_remove(channel=channel, timestamp=timestamp, name=name)
    except Exception:
        logger.warning("Failed to remove :%s: reaction", name, exc_info=True)


def verify_request(body: str, timestamp: str | None, signature: str | None) -> bool:
    """Slack signing-secret check (HMAC-SHA256, rejects requests older than 5 minutes)."""
    if not timestamp or not signature:
        return False
    verifier = SignatureVerifier(signing_secret=slack_credentials().signing_secret)
    return verifier.is_valid(body=body, timestamp=timestamp, signature=signature)


# A person's message with files attached arrives with the file_share subtype. Every other
# subtype is an edit, a deletion, a join or the like, which the bot doesn't answer.
_PERSON_SUBTYPES = {None, "file_share"}


def _from_a_person(event: dict) -> bool:
    """Excludes bots (including this one), edits, deletions, joins and other subtypes."""
    return bool(event.get("user")) and not event.get("bot_id") and event.get("subtype") in _PERSON_SUBTYPES


def should_handle(event: dict) -> bool:
    """Messages that are always for the bot: @mentions in channels and direct messages."""
    if not _from_a_person(event):
        return False
    if event.get("type") == "app_mention":
        return True
    return event.get("type") == "message" and event.get("channel_type") == "im"


def is_channel_message(event: dict) -> bool:
    """Any message a person posts in a public or private channel the bot is in."""
    return (
        _from_a_person(event)
        and event.get("type") == "message"
        and event.get("channel_type") in ("channel", "group")
    )


def mentioned_users(text: str) -> set[str]:
    return set(_MENTION.findall(text or ""))


def bot_user_id(payload: dict) -> str:
    """The bot's own Slack user ID, from the event's authorizations or, failing that, auth.test."""
    for authorization in payload.get("authorizations") or []:
        if authorization.get("is_bot") and authorization.get("user_id"):
            return authorization["user_id"]
    return _auth_test_user_id()


@lru_cache(maxsize=1)
def _auth_test_user_id() -> str:
    return slack_client().auth_test()["user_id"]


def acknowledge(client, team_id: str, channel: str, message_ts: str, thread_ts: str) -> str:
    """Show the user we're on it and return the placeholder's ts for the worker to update.

    Reacts to their message, posts the placeholder in the thread, and remembers the thread
    so that follow-ups in it no longer need an @mention.
    """
    add_reaction(client, channel, message_ts, REACTION_WORKING)
    placeholder = client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=PLACEHOLDER_TEXT)
    mark_engaged(team_id, channel, thread_ts)
    return placeholder["ts"]


def clean_text(text: str) -> str:
    return _MENTION.sub("", text or "").strip()
