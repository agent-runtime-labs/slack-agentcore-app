"""POST /slack/events — Slack Events API entry point.

Must answer within 3 seconds, so it only verifies, decides how to route the message,
and queues the real work for agent_worker:

  * reply   -- @mentions and DMs. Reacts and posts a placeholder now.
  * triage  -- any other message a person posts in a channel the bot is in, including
               follow-ups in threads the bot is part of. Queued silently; agent_worker
               asks the model whether it is meant for the bot and only then reacts and
               replies.
"""

import json
import logging

from slack_app.apigw import header, json_response, raw_body
from slack_app.engaged_threads import is_engaged
from slack_app.slack import (
    acknowledge,
    bot_user_id,
    clean_text,
    is_channel_message,
    mentioned_users,
    should_handle,
    slack_client,
    verify_request,
)
from slack_app.work_queue import enqueue

logger = logging.getLogger(__name__)

OK = json_response(200, {"ok": True})

REPLY = "reply"
TRIAGE = "triage"


def handler(event: dict, context) -> dict:
    body = raw_body(event)
    # Slack signs url_verification too, so verify before anything else.
    if not verify_request(body, header(event, "x-slack-request-timestamp"), header(event, "x-slack-signature")):
        logger.warning("Rejected request with invalid Slack signature")
        return json_response(401, {"error": "invalid signature"})

    payload = json.loads(body)
    if payload.get("type") == "url_verification":
        return json_response(200, {"challenge": payload["challenge"]})

    if header(event, "x-slack-retry-num"):
        # The first delivery was already queued; Slack retries when we are slow.
        logger.info("Ignoring Slack retry %s", header(event, "x-slack-retry-num"))
        return OK

    slack_event = payload.get("event") or {}
    if payload.get("type") != "event_callback":
        return OK

    text = clean_text(slack_event.get("text", ""))
    if not text:
        return OK

    team_id = payload.get("team_id") or slack_event.get("team", "")
    route, bot_in_thread = _route(payload, slack_event, team_id)
    if not route:
        return OK

    channel = slack_event["channel"]
    user_message_ts = slack_event["ts"]
    thread_ts = slack_event.get("thread_ts") or user_message_ts

    job = {
        "team_id": team_id,
        "channel": channel,
        "user": slack_event["user"],
        "thread_ts": thread_ts,
        "text": text,
        "user_message_ts": user_message_ts,
    }
    if route == REPLY:
        # Near-instant feedback, before the 3-second budget is spent on anything else.
        # agent_worker swaps the reaction for an outcome reaction once it's done.
        job["placeholder_ts"] = acknowledge(slack_client(), team_id, channel, user_message_ts, thread_ts)
    else:
        job["triage"] = {"text": slack_event["text"], "bot_in_thread": bot_in_thread}

    enqueue(job, group_id=f"{channel}-{thread_ts}", dedup_id=payload.get("event_id") or slack_event["ts"])
    logger.info("Queued event %s from user %s (%s)", payload.get("event_id"), slack_event["user"], route)
    return OK


def _route(payload: dict, event: dict, team_id: str) -> tuple[str | None, bool]:
    """(REPLY | TRIAGE | None to ignore, whether the bot is already part of the thread)."""
    if should_handle(event):
        return REPLY, False
    if not is_channel_message(event):
        return None, False

    if bot_user_id(payload) in mentioned_users(event.get("text", "")):
        # Slack sends this message a second time as an app_mention; that copy replies.
        return None, False

    # Even in a thread the bot is part of, a follow-up may be for a person: "Philip, can
    # you check this?" names them in plain text, not as an @mention. Knowing the bot is
    # in the thread tilts triage towards replying to follow-ups that don't name anyone.
    thread_ts = event.get("thread_ts")
    bot_in_thread = bool(thread_ts) and is_engaged(team_id, event["channel"], thread_ts)
    return TRIAGE, bot_in_thread
