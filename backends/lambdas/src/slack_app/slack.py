"""Slack Web API client and request signature verification."""

import logging
import re
import time
from functools import lru_cache

from slack_sdk import WebClient
from slack_sdk.signature import SignatureVerifier

from slack_app.config import slack_credentials, slack_dry_run

logger = logging.getLogger(__name__)

_MENTION = re.compile(r"<@[A-Z0-9]+>")

# Reaction names (Slack emoji shortcodes, no colons) for the lifecycle of a request:
# added the moment we receive it, then swapped for one of the outcome reactions once
# agent_worker finishes. REACTION_DONE deliberately isn't a check mark: this is an AI
# reply in a channel with human collaborators, and a check mark reads as "verified
# correct" rather than "a reply was posted" -- speech_balloon makes no such claim.
REACTION_WORKING = "eyes"
REACTION_DONE = "speech_balloon"
REACTION_ERROR = "warning"
REACTION_AUTH_REQUIRED = "lock"


class DryRunSlackClient:
    """Stands in for WebClient when SLACK_DRY_RUN=true; logs instead of calling Slack."""

    def _log(self, method: str, **kwargs) -> dict:
        logger.info("[slack dry-run] %s %s", method, kwargs)
        return {"ok": True, "ts": f"{time.time():.6f}"}

    def chat_postMessage(self, **kwargs) -> dict:  # noqa: N802 - mirrors slack_sdk
        return self._log("chat.postMessage", **kwargs)

    def chat_update(self, **kwargs) -> dict:
        return self._log("chat.update", **kwargs)

    def chat_postEphemeral(self, **kwargs) -> dict:  # noqa: N802
        return self._log("chat.postEphemeral", **kwargs)

    def reactions_add(self, **kwargs) -> dict:
        return self._log("reactions.add", **kwargs)

    def reactions_remove(self, **kwargs) -> dict:
        return self._log("reactions.remove", **kwargs)


@lru_cache(maxsize=1)
def slack_client() -> WebClient | DryRunSlackClient:
    if slack_dry_run():
        return DryRunSlackClient()
    return WebClient(token=slack_credentials().bot_token)


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


def should_handle(event: dict) -> bool:
    """Only real user messages: @mentions in channels and direct messages."""
    if event.get("bot_id") or event.get("subtype") or not event.get("user"):
        return False
    if event.get("type") == "app_mention":
        return True
    return event.get("type") == "message" and event.get("channel_type") == "im"


def clean_text(text: str) -> str:
    return _MENTION.sub("", text or "").strip()
