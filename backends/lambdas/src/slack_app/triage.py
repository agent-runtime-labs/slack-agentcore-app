"""Decides whether a channel message that didn't @mention the bot is meant for it.

Runs in agent_worker, never in the 3-second slack_events handler: it is one short
Bedrock call per message. It errs on the side of staying quiet, so any failure is a
"no". A message crafted to make this say "yes" gets no more than an @mention would.
"""

import logging
import os
import re
from functools import lru_cache

import boto3
from botocore.config import Config

logger = logging.getLogger(__name__)

# Not Nova Micro (the chat model): told that the bot is in the thread, Micro answered
# YES to follow-ups addressed to a person ("Philip do you have access to it"), whatever
# the prompt said. Nova Lite, Nova Pro and Claude Haiku 4.5 all got this right; Lite is
# the cheapest of them.
DEFAULT_MODEL_ID = "us.amazon.nova-lite-v1:0"

DEFAULT_ASSISTANT_NAME = "AgentCore Assistant"

SYSTEM_PROMPT = """\
You screen messages in a Slack channel for an AI assistant named "{name}". The \
assistant is a member of the channel, where people mostly talk to each other. It \
answers general questions and can look things up in the asker's own LinkedIn, GitHub, \
Linear and Notion accounts.

Decide whether the assistant should reply. Go through these checks in order and stop \
at the first one that matches:

1. The message is addressed to a person: it starts or ends with a name, or \
@someone, that is not "{name}", "bot", "assistant" or "AI". People's names such as \
Philip or Sarah are never the assistant. -> NO, even inside a thread the assistant \
has been answering in.
2. The message is addressed to the assistant by name ("{name}", "bot", "assistant", \
"AI"). -> YES
3. The message is an acknowledgement, chit-chat, an announcement or a status update \
("thanks", "ok", "lol", "standup in 5", "I'm out tomorrow"). -> NO
4. It is a reply in a thread the assistant has been answering in, and it asks a \
question or makes a request, however short ("why?", "and my GitHub?"). -> YES
5. It is a question to the channel that the assistant can clearly answer or look up. \
-> YES. A question only the people here can answer (opinions, availability, plans) \
-> NO.
6. Anything else. -> NO

Examples:
- In the assistant's thread: "Philip do you have access to it" -> NO (check 1)
- In the assistant's thread: "Sarah, what do you think?" -> NO (check 1)
- In the assistant's thread: "do you have access to it?" -> YES (check 4)
- In the assistant's thread: "which one is the oldest?" -> YES (check 4)
- In the channel: "bot, what's my GitHub username?" -> YES (check 2)
- In the channel: "lunch anyone?" -> NO (check 5)
- In the channel: "how do I connect Notion here?" -> YES (check 5)

Reply with exactly one word: YES or NO."""

_USER_MENTION = re.compile(r"<@[A-Z0-9]+(?:\|[^>]*)?>")
_BROADCAST = re.compile(r"<!(here|channel|everyone)(?:\|[^>]*)?>")


def triage_model_id() -> str:
    return os.getenv("TRIAGE_MODEL_ID", DEFAULT_MODEL_ID)


def assistant_name() -> str:
    """The bot's display name in Slack (docs/slack-app-manifest.yaml), so triage can tell it from people's names."""
    return os.getenv("ASSISTANT_NAME", DEFAULT_ASSISTANT_NAME)


@lru_cache(maxsize=1)
def _bedrock():
    return boto3.client("bedrock-runtime", config=Config(read_timeout=10, retries={"total_max_attempts": 2}))


def wants_reply(text: str, bot_in_thread: bool) -> bool:
    where = (
        "a reply in a thread the assistant has been answering in"
        if bot_in_thread
        else "a message in the channel the assistant has not been asked about yet"
    )
    prompt = f"This is {where}.\n\n<message>\n{_readable(text)}\n</message>\n\nShould the assistant reply?"
    try:
        response = _bedrock().converse(
            modelId=triage_model_id(),
            system=[{"text": SYSTEM_PROMPT.format(name=assistant_name())}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 5, "temperature": 0},
        )
        answer = response["output"]["message"]["content"][0]["text"]
    except Exception:
        logger.warning("Triage call failed; staying quiet", exc_info=True)
        return False
    return answer.strip().upper().startswith("YES")


def _readable(text: str) -> str:
    """Slack markup -> what a reader sees. Mentions of the bot never get this far."""
    text = _USER_MENTION.sub("@someone", text)
    return _BROADCAST.sub(lambda match: f"@{match.group(1)}", text)
