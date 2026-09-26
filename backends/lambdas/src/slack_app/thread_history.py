"""Reads the Slack thread a message belongs to, so the bot sees what people see.

The thread is the bot's only conversation history: triage reads it to tell whether a
message is meant for the bot, and the agent reads it to answer. Nothing is stored in
between. Every job reads the thread from Slack again, so a follow-up an hour later, or
from a different person, has the same context as one sent straight away.

Files people attached stay in the thread as references (attachments.py): the agent
downloads one only when the question needs it.

Reading is best-effort. If Slack can't be reached, the bot carries on with just the new
message, as it did before it read threads at all.
"""

import logging
import re
from dataclasses import dataclass
from decimal import Decimal

from slack_app.attachments import Attachment, describe, from_files
from slack_app.config import assistant_name
from slack_app.slack import PLACEHOLDER_TEXT

logger = logging.getLogger(__name__)

# Enough for a long discussion without making every triage call expensive. The first
# message is always kept, because it usually says what the thread is about.
MAX_MESSAGES = 30
MAX_CHARS = 2000
# conversations.replies pages oldest first. Stop after this many pages (1,000 messages).
MAX_PAGES = 5
PAGE_SIZE = 200

# Subtypes that are people (or bots) saying something. The rest are joins, topic
# changes, deletions and the like.
_CONVERSATION_SUBTYPES = {None, "bot_message", "thread_broadcast", "file_share", "me_message"}

_USER_MENTION = re.compile(r"<@([A-Z0-9]+)(?:\|([^>]*))?>")
_BROADCAST = re.compile(r"<!(here|channel|everyone)(?:\|[^>]*)?>")


@dataclass(frozen=True)
class ThreadMessage:
    author: str
    text: str
    from_assistant: bool = False
    files: tuple[Attachment, ...] = ()

    def as_dict(self) -> dict:
        message = {"author": self.author, "text": self.text, "fromAssistant": self.from_assistant}
        if self.files:
            message["files"] = [file.as_dict() for file in self.files]
        return message

    @property
    def with_files(self) -> str:
        """The text plus a line naming any files, as triage reads it."""
        return " ".join(part for part in (self.text, describe(self.files)) if part)


@dataclass(frozen=True)
class Thread:
    history: list[ThreadMessage]
    """Messages before the new one, oldest first."""
    message: ThreadMessage | None = None
    """The new message itself, or None if Slack didn't return it."""

    @property
    def bot_in_thread(self) -> bool:
        return any(message.from_assistant for message in self.history)


def read_thread(client, channel: str, thread_ts: str, message_ts: str, bot_user_id: str | None) -> Thread:
    """The thread up to and including message_ts. Later messages are left out, including our own placeholder."""
    try:
        raw = _replies(client, channel, thread_ts, message_ts)
    except Exception:
        logger.warning("Failed to read thread %s; carrying on without it", thread_ts, exc_info=True)
        return Thread(history=[])

    names = _names(raw, bot_user_id)
    cutoff = Decimal(message_ts)
    new, earlier = None, []
    for item in raw:
        ts = Decimal(item.get("ts") or "0")
        if ts > cutoff or item.get("subtype") not in _CONVERSATION_SUBTYPES:
            continue
        message = _to_message(item, names, bot_user_id)
        if ts == cutoff:
            new = message
        elif (message.text or message.files) and not (message.from_assistant and message.text == PLACEHOLDER_TEXT):
            earlier.append(message)

    if len(earlier) > MAX_MESSAGES:
        earlier = earlier[:1] + earlier[-(MAX_MESSAGES - 1) :]
    return Thread(history=earlier, message=new)


def readable(text: str, names: dict[str, str] | None = None) -> str:
    """Slack markup -> what a reader sees: "<@U123>" becomes "@Alice", or "@someone" if the name is unknown."""
    names = names or {}
    text = _USER_MENTION.sub(lambda match: "@" + (names.get(match.group(1)) or match.group(2) or "someone"), text or "")
    return _BROADCAST.sub(lambda match: f"@{match.group(1)}", text).strip()


def _replies(client, channel: str, thread_ts: str, message_ts: str) -> list[dict]:
    messages: list[dict] = []
    cursor = None
    for _ in range(MAX_PAGES):
        kwargs = {"cursor": cursor} if cursor else {}
        response = client.conversations_replies(
            channel=channel, ts=thread_ts, latest=message_ts, inclusive=True, limit=PAGE_SIZE, **kwargs
        )
        messages += response.get("messages") or []
        cursor = (response.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            return messages
    logger.warning("Thread %s is longer than %d messages; reading only the start", thread_ts, len(messages))
    return messages


def _names(raw: list[dict], bot_user_id: str | None) -> dict[str, str]:
    """Slack user ID -> display name. Slack includes the author's profile in their messages, so no users:read scope."""
    names = {bot_user_id: assistant_name()} if bot_user_id else {}
    for item in raw:
        profile = item.get("user_profile") or {}
        name = profile.get("display_name") or profile.get("real_name")
        if item.get("user") and name:
            names.setdefault(item["user"], name)
    return names


def _to_message(item: dict, names: dict[str, str], bot_user_id: str | None) -> ThreadMessage:
    user = item.get("user")
    from_assistant = bool(bot_user_id) and user == bot_user_id
    if user:
        author = names.get(user, user)
    else:
        author = (item.get("bot_profile") or {}).get("name") or item.get("username") or "a bot"
    text = readable(item.get("text", ""), names)
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "…"
    return ThreadMessage(author=author, text=text, from_assistant=from_assistant, files=from_files(item.get("files")))
