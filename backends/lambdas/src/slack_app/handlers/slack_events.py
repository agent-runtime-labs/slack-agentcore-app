"""POST /slack/events — Slack Events API entry point.

Must answer within 3 seconds, so it only verifies, reacts, posts a placeholder and
queues the real work for agent_worker.
"""

import json
import logging

from slack_app.apigw import header, json_response, raw_body
from slack_app.slack import REACTION_WORKING, add_reaction, clean_text, should_handle, slack_client, verify_request
from slack_app.work_queue import enqueue

logger = logging.getLogger(__name__)

OK = json_response(200, {"ok": True})


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

    if slack_event.get("type") == "app_home_opened" and slack_event.get("tab") == "home":
        team_id = payload.get("team_id") or slack_event.get("team", "")
        user = slack_event["user"]
        enqueue(
            {"type": "app_home", "team_id": team_id, "user": user},
            group_id=f"home-{team_id}-{user}",
            dedup_id=payload.get("event_id") or f"{user}-{slack_event.get('event_ts', '')}",
        )
        return OK

    if not should_handle(slack_event):
        return OK

    text = clean_text(slack_event.get("text", ""))
    if not text:
        return OK

    team_id = payload.get("team_id") or slack_event.get("team", "")
    channel = slack_event["channel"]
    user_message_ts = slack_event["ts"]
    thread_ts = slack_event.get("thread_ts") or user_message_ts

    slack = slack_client()
    # Near-instant feedback that we got the message, before the 3-second budget is spent
    # on anything else. agent_worker swaps this for an outcome reaction once it's done.
    add_reaction(slack, channel, user_message_ts, REACTION_WORKING)

    placeholder = slack.chat_postMessage(channel=channel, thread_ts=thread_ts, text="🤔 Thinking…")

    job = {
        "team_id": team_id,
        "channel": channel,
        "user": slack_event["user"],
        "thread_ts": thread_ts,
        "text": text,
        "placeholder_ts": placeholder["ts"],
        "user_message_ts": user_message_ts,
    }
    enqueue(job, group_id=f"{channel}-{thread_ts}", dedup_id=payload.get("event_id") or slack_event["ts"])
    logger.info("Queued event %s from user %s", payload.get("event_id"), slack_event["user"])
    return OK
