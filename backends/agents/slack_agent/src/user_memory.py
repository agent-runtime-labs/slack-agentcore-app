"""Per-user memory: what one person told the bot about themselves, kept across threads.

AgentCore Memory holds it. After a reply, remember() saves the turn as a short-term
event, and AgentCore extracts long-term records from those events in the background:
preferences (how they like answers, what they use) and facts (their role, what they own).
Before the next reply, recall() reads them back, and thread_prompt.py shows them to the
model as notes about the requester.

Nothing happens without the person's consent, which the agent_worker Lambda checks and
states in the payload's "memory" field (slack_app/user_memory/ there):

    {"granted": true, "learn": "public", "recall": ["public"]}

`learn` is the tier this conversation is saved into and `recall` the tiers that may be
read here. A tier is where something was said ("dm", "public" or a channel ID), and each
is its own AgentCore actor, so what was said in a DM can't surface in a channel. The
worker decides both: the model never chooses what it may see.

Only two things are ever saved: the requester's own latest message, and the bot's answer
to it. Not the thread (other people wrote that), not files, not web pages. If a tool ran,
the answer is replaced by a stub, so nothing from someone's GitHub, Linear, Notion or
LinkedIn account, or from another thread, ends up in memory.

This is not Strands' AgentCoreMemorySessionManager: that saves every message the agent
sees, and here the user turn carries the whole Slack thread, so other people's words
would be remembered as the requester's.

Everything here is best-effort. A failure means an answer without memory, never a
failed answer.
"""

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache

import boto3
from botocore.config import Config
from strands import tool

import config

logger = logging.getLogger(__name__)

# Must match the strategies' namespaces in infra-as-code/tf-app/memory.tf and
# slack_app/user_memory/__init__.py in the Lambdas.
PREFERENCES = "/users/{actor}/preferences/"
FACTS = "/users/{actor}/facts/"

MAX_PREFERENCES = 20
MAX_FACTS = 5
MAX_NOTES = 12
MAX_NOTE_CHARS = 300
MAX_QUERY_CHARS = 1000
MAX_SAVED_CHARS = 4000
MAX_TIERS = 8

_TIER = re.compile(r"^(dm|public|[A-Z][A-Z0-9]{2,20})$")

# Tools whose results never come from an account, a file, a page or another thread.
_HARMLESS_TOOLS = {"draw_diagram", "memory_settings"}
TOOL_STUB = "(Answered using tools. The answer isn't kept.)"


@dataclass(frozen=True)
class Scope:
    user_id: str
    learn: str | None
    recall: tuple[str, ...]

    @classmethod
    def from_payload(cls, value: object, user_id: str) -> "Scope | None":
        """The payload's "memory" field, or None (no memory) unless consent is granted and the tiers are well-formed."""
        if not config.USER_MEMORY_ID or not isinstance(value, dict) or value.get("granted") is not True:
            return None
        learn, recall = value.get("learn"), value.get("recall") or []
        if learn is not None and not _is_tier(learn):
            return None
        if not isinstance(recall, list) or not all(_is_tier(tier) for tier in recall):
            return None
        if learn is None and not recall:
            return None
        return cls(user_id, learn, tuple(recall[:MAX_TIERS]))

    def actor(self, tier: str) -> str:
        return f"{self.user_id}-{tier}"


def _is_tier(value: object) -> bool:
    return isinstance(value, str) and bool(_TIER.match(value))


@lru_cache(maxsize=1)
def _memory():
    # Short timeouts and no retries: recall sits in front of every answer.
    return boto3.client(
        "bedrock-agentcore",
        region_name=config.AWS_REGION,
        config=Config(connect_timeout=2, read_timeout=3, retries={"total_max_attempts": 1}),
    )


def recall(scope: Scope, query: str) -> list[str]:
    """Notes about the requester: every preference, plus the facts closest to the query."""
    query = " ".join((query or "").split())[:MAX_QUERY_CHARS]
    lookups = []
    for tier in scope.recall:
        actor = scope.actor(tier)
        lookups.append((_preferences, actor, ""))
        if query:
            lookups.append((_facts, actor, query))
    if not lookups:
        return []
    with ThreadPoolExecutor(max_workers=len(lookups)) as pool:
        found = list(pool.map(lambda lookup: lookup[0](lookup[1], lookup[2]), lookups))
    notes: list[str] = []
    # Preferences first: they apply to every answer, facts only to some.
    for wanted in (_preferences, _facts):
        for (lookup, _, _), texts in zip(lookups, found, strict=True):
            if lookup is wanted:
                notes += [text for text in texts if text not in notes]
    logger.info("Recalled %d notes from %d tiers", len(notes), len(scope.recall))
    return notes[:MAX_NOTES]


def _preferences(actor: str, _query: str) -> list[str]:
    """Listed, not searched: "prefers short answers" matters whatever the question is about."""
    try:
        response = _memory().list_memory_records(
            memoryId=config.USER_MEMORY_ID, namespace=PREFERENCES.format(actor=actor), maxResults=MAX_PREFERENCES
        )
    except Exception:
        logger.warning("Listing preferences failed; answering without them", exc_info=True)
        return []
    return _texts(response.get("memoryRecordSummaries") or [])


def _facts(actor: str, query: str) -> list[str]:
    try:
        response = _memory().retrieve_memory_records(
            memoryId=config.USER_MEMORY_ID,
            namespace=FACTS.format(actor=actor),
            searchCriteria={"searchQuery": query, "topK": MAX_FACTS},
        )
    except Exception:
        logger.warning("Searching facts failed; answering without them", exc_info=True)
        return []
    records = response.get("memoryRecordSummaries") or []
    return _texts([record for record in records if float(record.get("score") or 0) >= config.USER_MEMORY_MIN_SCORE])


def _texts(records: list[dict]) -> list[str]:
    texts = []
    for record in records:
        text = _readable((record.get("content") or {}).get("text") or "")
        if text:
            texts.append(text[:MAX_NOTE_CHARS])
    return texts


def _readable(text: str) -> str:
    """Preference records are JSON ({"context", "preference", "categories"}); facts are plain text."""
    text = text.strip()
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            return text
        if isinstance(data, dict) and isinstance(data.get("preference"), str):
            return data["preference"].strip()
    return text


def remember(scope: Scope, session_id: str, said: str, answer: str, tools_used: set[str]) -> None:
    """Save the requester's message and the answer to it, for AgentCore to learn from."""
    said = (said or "").strip()[:MAX_SAVED_CHARS]
    if not scope.learn or not said:
        return
    if tools_used - _HARMLESS_TOOLS:
        answer = TOOL_STUB
    turns = [("USER", said), ("ASSISTANT", (answer or "").strip()[:MAX_SAVED_CHARS])]
    try:
        _memory().create_event(
            memoryId=config.USER_MEMORY_ID,
            actorId=scope.actor(scope.learn),
            sessionId=session_id,
            eventTimestamp=datetime.now(UTC),
            payload=[{"conversational": {"content": {"text": text}, "role": role}} for role, text in turns if text],
        )
    except Exception:
        logger.warning("Saving the turn to memory failed; nothing was remembered", exc_info=True)


def tools_used(messages: object) -> set[str]:
    """Names of the tools the agent called, from its message list."""
    names = set()
    for message in messages if isinstance(messages, list) else []:
        for block in (message.get("content") or []) if isinstance(message, dict) else []:
            if isinstance(block, dict) and isinstance(block.get("toolUse"), dict):
                names.add(str(block["toolUse"].get("name") or ""))
    return names


@dataclass
class SettingsRequest:
    """Set when the user asks about their memory settings; main.py passes it on to the worker."""

    requested: bool = False


def build_memory_settings_tool(request: SettingsRequest):
    @tool
    def memory_settings() -> str:
        """Send the user their private memory settings, with buttons to turn remembering on or off.

        Call it when the user asks to turn memory on or off, to forget what you know about
        them, what you keep about them, or to remember something while memory is off. Only
        they can change the setting, with the buttons: you can't change it for them.
        """
        request.requested = True
        return (
            "The user is being sent a private message with buttons to turn memory on or off. Tell them in one "
            "short line to use it. Don't say that anything was saved, changed or deleted: only the buttons do that."
        )

    return memory_settings
