"""SQS consumer — calls the agent as the Slack user and writes the answer back to Slack.

Triage jobs (channel messages that didn't @mention the bot, see slack_events.py) first
ask the model whether the message is meant for the bot. Only if it is do they get the
reaction and placeholder that other jobs got up front; otherwise the bot stays quiet.

invoke_agent is a single blocking call, so we pass the placeholder's channel/ts along and
let the agent post its own live per-tool progress directly to Slack (see slack_progress.py
in the agent). As a fallback for turns where the agent never gets to post anything (no tool
calls, a missing bot token in the agent's environment, ...), a timer here nudges the
placeholder once if the call is still running past INTERIM_DELAY_SECONDS.
"""

import json
import logging
import threading
import time

from slack_app.agent_client import invoke_agent
from slack_app.config import public_base_url
from slack_app.identity import runtime_session_id, runtime_user_id
from slack_app.pending_auth import TTL_SECONDS, PendingAuth, new_nonce, pending_auth_store
from slack_app.slack import (
    REACTION_AUTH_REQUIRED,
    REACTION_DONE,
    REACTION_ERROR,
    REACTION_WORKING,
    acknowledge,
    add_reaction,
    remove_reaction,
    slack_client,
)
from slack_app.triage import wants_reply

logger = logging.getLogger(__name__)

INTERIM_DELAY_SECONDS = 6
INTERIM_TEXT = "🔎 Still working on it — checking tools and thinking this through…"


def handler(event: dict, context) -> dict:
    for record in event.get("Records", []):
        process(json.loads(record["body"]))
    return {"batchItemFailures": []}


def process(job: dict) -> None:
    slack = slack_client()
    triage = job.get("triage")
    if triage:
        if not wants_reply(triage["text"], triage["bot_in_thread"]):
            logger.info("Not replying to message %s: not meant for the bot", job["user_message_ts"])
            return
        logger.info("Replying to message %s: triage says it's meant for the bot", job["user_message_ts"])
        placeholder_ts = acknowledge(slack, job["team_id"], job["channel"], job["user_message_ts"], job["thread_ts"])
        job = {**job, "placeholder_ts": placeholder_ts}

    user_id = runtime_user_id(job["team_id"], job["user"])
    session_id = runtime_session_id(job["team_id"], job["channel"], job["thread_ts"], job["user"])

    timer = threading.Timer(INTERIM_DELAY_SECONDS, _interim_update, args=(slack, job))
    timer.daemon = True
    timer.start()
    try:
        result = invoke_agent(job["text"], user_id, session_id, job["channel"], job["placeholder_ts"])
    except Exception:
        # Don't re-raise: an SQS retry would run the agent again and double-post.
        logger.exception("Agent invocation failed")
        _update(slack, job, "⚠️ Sorry, something went wrong while talking to the agent. Please try again.")
        _finish_reaction(slack, job, REACTION_ERROR)
        return
    finally:
        timer.cancel()

    auth = result.get("authRequired")
    if auth:
        provider = auth.get("provider") or "that"
        _send_connect_link(slack, job, user_id, auth)
        text = (
            f"🔐 <@{job['user']}> I need access to your {provider} account first. "
            "I've sent you a private link — ask me again once you've connected."
        )
        _finish_reaction(slack, job, REACTION_AUTH_REQUIRED)
    else:
        text = result.get("message") or result.get("error") or "I didn't get a response."
        _finish_reaction(slack, job, REACTION_DONE)

    _update(slack, job, text)


def _send_connect_link(slack, job: dict, user_id: str, auth: dict) -> None:
    provider = auth.get("provider") or "your account"
    pending = PendingAuth(
        nonce=new_nonce(),
        runtime_user_id=user_id,
        provider=provider,
        # Exactly one of these is set: AgentCore Identity gives us a session URI,
        # a CIMD provider gives us the handoff payload the callback needs.
        session_uri=auth.get("sessionUri") or "",
        cimd=auth.get("cimd"),
        authorization_url=auth["authorizationUrl"],
        channel=job["channel"],
        slack_user=job["user"],
        thread_ts=job["thread_ts"],
        expires_at=int(time.time()) + TTL_SECONDS,
    )
    pending_auth_store().put(pending)
    link = f"{public_base_url()}/oauth2/start?nonce={pending.nonce}"

    # Ephemeral: only the requesting user can see (and use) this link.
    slack.chat_postEphemeral(
        channel=job["channel"],
        user=job["user"],
        thread_ts=job["thread_ts"],
        text=f"Connect your {provider} account: {link}",
        blocks=[
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Connect {provider}* — this link is just for you and expires in 10 minutes.",
                },
                "accessory": {
                    "type": "button",
                    "text": {"type": "plain_text", "text": f"Connect {provider}"},
                    "url": link,
                    "style": "primary",
                },
            }
        ],
    )


def _update(slack, job: dict, text: str) -> None:
    try:
        slack.chat_update(channel=job["channel"], ts=job["placeholder_ts"], text=text)
    except Exception:
        logger.exception("Failed to update Slack message")


def _finish_reaction(slack, job: dict, name: str) -> None:
    """Swap the 👀 reaction on the user's message for the outcome reaction.

    add_reaction/remove_reaction are already best-effort (a missing reactions:write scope
    or any API error is logged, not raised), and older queued jobs may predate the
    user_message_ts field, so this is a no-op rather than a failure in either case.
    """
    ts = job.get("user_message_ts")
    if not ts:
        return
    remove_reaction(slack, job["channel"], ts, REACTION_WORKING)
    add_reaction(slack, job["channel"], ts, name)


def _interim_update(slack, job: dict) -> None:
    # Runs on the timer thread; the agent call may finish (and this may even fire) after
    # process() has already moved on, so failures here are logged and otherwise ignored.
    try:
        slack.chat_update(channel=job["channel"], ts=job["placeholder_ts"], text=INTERIM_TEXT)
    except Exception:
        logger.exception("Failed to post interim update")
