"""Best-effort live progress updates to Slack while the agent works.

The agent runs in AgentCore Runtime, a separate process from the slack-app Lambda that
owns the placeholder message (see agent_worker.INTERIM_DELAY_SECONDS for that Lambda's
blind timer-based guess). This module lets the agent post directly to Slack's
chat.update API each time Strands starts a new tool call, so the user sees what's
actually happening ("Checking GitHub...") instead of a generic "still working" nudge.

Nothing here may ever break the real answer: a missing channel/ts, a missing bot
token, or a network error are all swallowed rather than raised.
"""

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from functools import lru_cache
from typing import Any

import boto3

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 5
SLACK_CHAT_UPDATE_URL = "https://slack.com/api/chat.update"

# One friendly emoji per known tool; anything else (a future CIMD provider) falls back
# to a plain magnifying glass rather than needing a new entry here.
_TOOL_EMOJI = {
    "use_github": "\U0001f419",  # octopus
    "get_my_linkedin_profile": "\U0001f4bc",  # briefcase
    "use_linear": "\U0001f4cb",  # clipboard
    "use_notion": "\U0001f4dd",  # memo
}
_DEFAULT_TOOL_EMOJI = "\U0001f50e"  # magnifying glass

# Shown once the model starts producing its final answer, so the placeholder doesn't
# jump straight from the last "Checking..." line to the finished reply.
ANSWER_TEXT = "✨ Putting it all together…"


@lru_cache(maxsize=1)
def _bot_token() -> str:
    secret_arn = os.getenv("SLACK_SECRET_ARN")
    if secret_arn:
        raw = boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)["SecretString"]
        return json.loads(raw)["bot_token"]
    return os.getenv("SLACK_BOT_TOKEN", "")


def _friendly_tool_text(tool_name: str) -> str:
    emoji = _TOOL_EMOJI.get(tool_name, _DEFAULT_TOOL_EMOJI)
    label = tool_name.removeprefix("get_my_").removeprefix("use_").replace("_", " ").strip()
    return f"{emoji} Checking {label.title() or 'that'}…"


class ProgressReporter:
    """A Strands callback_handler that posts one chat.update per distinct tool call.

    Nova Micro can call several tools in the same turn (e.g. GitHub, then LinkedIn, then
    Linear) in quick succession, well under a second apart -- so updates are deduped by
    tool rather than throttled by a time window, which would silently drop every tool
    after the first.
    """

    def __init__(self, channel: str | None, ts: str | None):
        self._channel = channel
        self._ts = ts
        self._last_text: str | None = None

    def on_event(self, **kwargs: Any) -> None:
        tool_use = kwargs.get("event", {}).get("contentBlockStart", {}).get("start", {}).get("toolUse")
        name = tool_use.get("name") if tool_use else None
        if name:
            self._post(_friendly_tool_text(name))
        elif kwargs.get("data") or kwargs.get("reasoningText"):
            # The first token of the model's own text, after any tool calls -- ANSWER_TEXT
            # is a fixed string, so the dedup in _post naturally posts it only once, and
            # lets it post again later if another tool call interrupts it.
            self._post(ANSWER_TEXT)

    def _post(self, text: str) -> None:
        if not self._channel or not self._ts or text == self._last_text:
            return
        token = _bot_token()
        if not token:
            return
        self._last_text = text
        try:
            _post_form(SLACK_CHAT_UPDATE_URL, {"token": token, "channel": self._channel, "ts": self._ts, "text": text})
        except Exception:
            logger.exception("Failed to post progress update to Slack")


def _post_form(url: str, fields: dict[str, str]) -> None:
    body = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # nosec B310 - hardcoded https URL
        response.read()
