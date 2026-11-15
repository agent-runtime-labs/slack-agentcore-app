"""POST /slack/interactions — every button click in Slack (the app's Interactivity URL).

Slack posts here for any button the bot showed, link buttons such as "Connect GitHub"
included, and flags the button with a warning unless it gets a 200. So this is always
deployed, and acknowledges every signed click.

With USER_MEMORY_ENABLED it also acts on the buttons of the memory question
(user_memory/prompt.py), and is the only place a person's memory choice is written. Who
clicked comes from Slack's signed payload, never from the button, so nobody (and no
model) can answer for someone else.

Must answer within 3 seconds: it records the choice, replaces the question with a
confirmation, and queues the deletion for agent_worker when memory is turned off.
"""

import json
import logging
import urllib.parse
import urllib.request
import uuid

from slack_app.apigw import header, json_response, raw_body
from slack_app.config import slack_dry_run, user_memory_enabled
from slack_app.identity import runtime_user_id
from slack_app.slack import verify_request
from slack_app.user_memory import FORGET, prompt
from slack_app.user_memory.consent import DECLINED, GRANTED, consent_store
from slack_app.work_queue import enqueue

logger = logging.getLogger(__name__)

OK = {"statusCode": 200, "headers": {}, "body": ""}
TIMEOUT_SECONDS = 2
# Slack's own host for replying to an interaction. Anything else is not followed.
_RESPONSE_URL_PREFIX = "https://hooks.slack.com/"


def handler(event: dict, context) -> dict:
    body = raw_body(event)
    if not verify_request(body, header(event, "x-slack-request-timestamp"), header(event, "x-slack-signature")):
        logger.warning("Rejected interaction with invalid Slack signature")
        return json_response(401, {"error": "invalid signature"})

    payload = _payload(body)
    action = _action(payload)
    user = (payload.get("user") or {}).get("id")
    team = (payload.get("team") or {}).get("id") or (payload.get("user") or {}).get("team_id")
    if payload.get("type") != "block_actions" or not action or not user or not team:
        return OK
    if not user_memory_enabled():
        return OK  # nothing to act on: the click is only acknowledged

    user_id = runtime_user_id(team, user)
    store = consent_store()
    if action == prompt.GRANT:
        store.decide(user_id, GRANTED)
        text = prompt.GRANTED_TEXT
    elif action in (prompt.DECLINE, prompt.FORGET):
        before = store.get(user_id)
        store.decide(user_id, DECLINED)
        had_memory = bool(before and before.status == GRANTED)
        if had_memory:
            # A unique dedup ID: turning memory off twice must delete twice.
            enqueue({"kind": FORGET, "team_id": team, "user": user}, group_id=f"memory-{user_id}", dedup_id=uuid.uuid4().hex)
        text = prompt.FORGOTTEN_TEXT if had_memory else prompt.DECLINED_TEXT
    elif action == prompt.KEEP:
        text = prompt.KEPT_TEXT
    else:
        return OK

    logger.info("Memory choice %s recorded for a user", action)
    _respond(payload.get("response_url"), text)
    return OK


def _payload(body: str) -> dict:
    """Slack sends interactions as a form with one field, `payload`, holding JSON."""
    try:
        payload = json.loads(urllib.parse.parse_qs(body)["payload"][0])
    except (KeyError, IndexError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _action(payload: dict) -> str | None:
    actions = payload.get("actions") or []
    return actions[0].get("action_id") if actions and isinstance(actions[0], dict) else None


def _respond(response_url: object, text: str) -> None:
    """Replace the question with the outcome. Best-effort: the choice is already stored."""
    if slack_dry_run():
        logger.info("[slack dry-run] interaction response %s", text)
        return
    if not isinstance(response_url, str) or not response_url.startswith(_RESPONSE_URL_PREFIX):
        logger.warning("No usable response_url on the interaction; the choice is stored but not confirmed")
        return
    request = urllib.request.Request(
        response_url,
        data=json.dumps({"replace_original": True, "text": text}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # nosec B310 - host checked above
            response.read()
    except Exception:
        logger.warning("Failed to confirm the memory choice in Slack", exc_info=True)
