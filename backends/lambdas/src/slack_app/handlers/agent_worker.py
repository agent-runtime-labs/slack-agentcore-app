"""SQS consumer — calls the agent as the Slack user and writes the answer back to Slack."""

import json
import logging
import time

from slack_app.agent_client import invoke_agent
from slack_app.config import public_base_url
from slack_app.identity import runtime_session_id, runtime_user_id
from slack_app.pending_auth import TTL_SECONDS, PendingAuth, new_nonce, pending_auth_store
from slack_app.slack import slack_client

logger = logging.getLogger(__name__)


def handler(event: dict, context) -> dict:
    for record in event.get("Records", []):
        process(json.loads(record["body"]))
    return {"batchItemFailures": []}


def process(job: dict) -> None:
    slack = slack_client()
    user_id = runtime_user_id(job["team_id"], job["user"])
    session_id = runtime_session_id(job["team_id"], job["channel"], job["thread_ts"], job["user"])

    try:
        result = invoke_agent(job["text"], user_id, session_id)
    except Exception:
        # Don't re-raise: an SQS retry would run the agent again and double-post.
        logger.exception("Agent invocation failed")
        _update(slack, job, "⚠️ Sorry, something went wrong while talking to the agent. Please try again.")
        return

    auth = result.get("authRequired")
    if auth:
        _send_connect_link(slack, job, user_id, auth)
        text = (
            f"🔐 <@{job['user']}> I need access to your LinkedIn account first. "
            "I've sent you a private link — ask me again once you've connected."
        )
    else:
        text = result.get("message") or result.get("error") or "I didn't get a response."

    _update(slack, job, text)


def _send_connect_link(slack, job: dict, user_id: str, auth: dict) -> None:
    pending = PendingAuth(
        nonce=new_nonce(),
        runtime_user_id=user_id,
        session_uri=auth["sessionUri"],
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
        text=f"Connect your LinkedIn account: {link}",
        blocks=[
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Connect LinkedIn* — this link is just for you and expires in 10 minutes.",
                },
                "accessory": {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Connect LinkedIn"},
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
