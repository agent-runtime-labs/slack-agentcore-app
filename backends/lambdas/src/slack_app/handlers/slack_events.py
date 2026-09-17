"""POST /slack/events — Slack Events API entry point.

Must answer within 3 seconds, so it only verifies, posts a placeholder and
queues the real work for agent_worker.
"""

import json
import logging

from slack_app.apigw import header, json_response, raw_body
from slack_app.slack import clean_text, should_handle, slack_client, verify_request
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
    if payload.get("type") != "event_callback" or not should_handle(slack_event):
        return OK

    text = clean_text(slack_event.get("text", ""))
    if not text:
        return OK

    team_id = payload.get("team_id") or slack_event.get("team", "")
    channel = slack_event["channel"]
    thread_ts = slack_event.get("thread_ts") or slack_event["ts"]

    placeholder = slack_client().chat_postMessage(channel=channel, thread_ts=thread_ts, text="🤔 Thinking…")

    job = {
        "team_id": team_id,
        "channel": channel,
        "user": slack_event["user"],
        "thread_ts": thread_ts,
        "text": text,
        "placeholder_ts": placeholder["ts"],
    }
    enqueue(job, group_id=f"{channel}-{thread_ts}", dedup_id=payload.get("event_id") or slack_event["ts"])
    logger.info("Queued event %s from user %s", payload.get("event_id"), slack_event["user"])
    return OK
