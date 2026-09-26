"""SQS consumer — calls the agent as the Slack user and writes the answer back to Slack.

Every job starts by reading the Slack thread (thread_history.py): it is the only
conversation history the bot has, for triage and for the agent alike. Files attached to
the new message go to the agent as references; it downloads them itself.

Triage jobs (channel messages that didn't @mention the bot, see slack_events.py) then
ask the model what to do with the message (triage.py). REPLY gets the reaction and
placeholder that other jobs got up front, REACT gets a 👍 and nothing else, CORRECT gets
a short correction only if the agent confirms one is due, and IGNORE stays quiet.

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

from slack_app.agent_client import MODE_CORRECT, invoke_agent
from slack_app.attachments import from_files
from slack_app.config import public_base_url
from slack_app.identity import runtime_session_id, runtime_user_id
from slack_app.pending_auth import TTL_SECONDS, PendingAuth, new_nonce, pending_auth_store
from slack_app.slack import (
    REACTION_ACK,
    REACTION_AUTH_REQUIRED,
    REACTION_DONE,
    REACTION_ERROR,
    REACTION_WORKING,
    acknowledge,
    add_reaction,
    bot_user_id,
    remove_reaction,
    slack_client,
)
from slack_app.thread_history import Thread, ThreadMessage, read_thread, readable
from slack_app.triage import CORRECT, IGNORE, REACT, decide

logger = logging.getLogger(__name__)

INTERIM_DELAY_SECONDS = 6
INTERIM_TEXT = "🔎 Still working on it — checking tools and thinking this through…"


def handler(event: dict, context) -> dict:
    for record in event.get("Records", []):
        process(json.loads(record["body"]))
    return {"batchItemFailures": []}


def process(job: dict) -> None:
    slack = slack_client()
    thread = _read_thread(slack, job)
    # The new message as the thread shows it (names instead of <@U...>), and who sent it.
    prompt = thread.message.text if thread.message and thread.message.text else job["text"]
    requester = thread.message.author if thread.message else job["user"]
    files = thread.message.files if thread.message else from_files(job.get("files"))

    triage = job.get("triage")
    if triage:
        latest = thread.message or ThreadMessage(requester, readable(triage["text"]), files=files)
        new_text = latest.with_files
        in_thread = triage["bot_in_thread"] or thread.bot_in_thread
        action = decide(new_text, requester, thread.history, in_thread)
        logger.info("Triage says %s for message %s", action, job["user_message_ts"])
        if action == IGNORE:
            return
        if action == REACT:
            add_reaction(slack, job["channel"], job["user_message_ts"], REACTION_ACK)
            return
        if action == CORRECT:
            _correct(slack, job, prompt, requester, thread)
            return
        placeholder_ts = acknowledge(slack, job["team_id"], job["channel"], job["user_message_ts"], job["thread_ts"])
        job = {**job, "placeholder_ts": placeholder_ts}

    user_id = runtime_user_id(job["team_id"], job["user"])
    session_id = runtime_session_id(job["team_id"], job["channel"], job["thread_ts"], job["user"])

    timer = threading.Timer(INTERIM_DELAY_SECONDS, _interim_update, args=(slack, job))
    timer.daemon = True
    timer.start()
    try:
        result = invoke_agent(
            prompt,
            user_id,
            session_id,
            job["channel"],
            job["placeholder_ts"],
            thread=[message.as_dict() for message in thread.history],
            requester=requester,
            files=[file.as_dict() for file in files],
        )
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


def _read_thread(slack, job: dict) -> Thread:
    if not job.get("user_message_ts"):
        return Thread(history=[])  # queued before jobs carried the message's own ts
    return read_thread(slack, job["channel"], job["thread_ts"], job["user_message_ts"], bot_user_id({}))


def _correct(slack, job: dict, prompt: str, requester: str, thread: Thread) -> None:
    """Posts a short correction if the agent agrees one is due, and nothing otherwise.

    Nobody asked for this reply, so there's no reaction or placeholder up front, and a
    failure stays quiet instead of posting an error.
    """
    try:
        result = invoke_agent(
            prompt,
            runtime_user_id(job["team_id"], job["user"]),
            runtime_session_id(job["team_id"], job["channel"], job["thread_ts"], job["user"]),
            job["channel"],
            None,
            thread=[message.as_dict() for message in thread.history],
            requester=requester,
            mode=MODE_CORRECT,
        )
        correction = (result.get("message") or "").strip()
        if not correction:
            logger.info("Nothing to correct in message %s", job["user_message_ts"])
            return
        slack.chat_postMessage(channel=job["channel"], thread_ts=job["thread_ts"], text=correction)
    except Exception:
        logger.exception("Failed to post a correction; staying quiet")


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
