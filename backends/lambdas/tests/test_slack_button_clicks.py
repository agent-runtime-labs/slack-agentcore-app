import hashlib
import hmac
import json
import time
import urllib.parse

import pytest
from conftest import SIGNING_SECRET

from slack_app.handlers import slack_button_clicks
from slack_app.user_memory import prompt
from slack_app.user_memory.consent import DECLINED, GRANTED, consent_store

ALICE = "slack-T999-UALICE"


def click(action_id, *, user="UALICE", secret=SIGNING_SECRET, kind="block_actions"):
    payload = {
        "type": kind,
        "user": {"id": user, "team_id": "T999"},
        "team": {"id": "T999"},
        "actions": [{"action_id": action_id, "value": "slack-T999-UBOB"}],  # the value is never trusted
        "response_url": "https://hooks.slack.com/actions/T999/1/abc",
    }
    body = urllib.parse.urlencode({"payload": json.dumps(payload)})
    ts = str(int(time.time()))
    digest = hmac.new(secret.encode(), f"v0:{ts}:{body}".encode(), hashlib.sha256).hexdigest()
    headers = {"x-slack-request-timestamp": ts, "x-slack-signature": f"v0={digest}"}
    return {"rawPath": "/slack/interactions", "headers": headers, "body": body, "isBase64Encoded": False}


@pytest.fixture(autouse=True)
def memory_on(monkeypatch):
    monkeypatch.setenv("USER_MEMORY_ENABLED", "true")


@pytest.fixture
def queued(monkeypatch):
    jobs = []
    monkeypatch.setattr(slack_button_clicks, "enqueue", lambda job, group_id, dedup_id: jobs.append((job, group_id)))
    return jobs


@pytest.fixture
def replies(monkeypatch):
    texts = []
    monkeypatch.setattr(slack_button_clicks, "_respond", lambda url, text: texts.append(text))
    return texts


def test_a_click_with_a_bad_signature_changes_nothing(queued):
    result = slack_button_clicks.handler(click(prompt.GRANT, secret="wrong"), None)
    assert result["statusCode"] == 401
    assert consent_store().get(ALICE) is None


def test_yes_is_recorded_for_the_person_who_clicked(queued, replies):
    assert slack_button_clicks.handler(click(prompt.GRANT), None)["statusCode"] == 200

    assert consent_store().get(ALICE).granted
    assert consent_store().get("slack-T999-UBOB") is None
    assert replies == [prompt.GRANTED_TEXT]
    assert queued == []


def test_no_is_recorded_and_there_is_nothing_to_delete(queued, replies):
    slack_button_clicks.handler(click(prompt.DECLINE), None)
    assert consent_store().get(ALICE).status == DECLINED
    assert replies == [prompt.DECLINED_TEXT]
    assert queued == []


@pytest.mark.parametrize("action", [prompt.FORGET, prompt.DECLINE])
def test_turning_memory_off_stops_it_now_and_queues_the_deletion(queued, replies, action):
    consent_store().decide(ALICE, GRANTED)
    slack_button_clicks.handler(click(action), None)

    assert not consent_store().get(ALICE).granted
    assert queued == [({"kind": "forget_memory", "team_id": "T999", "user": "UALICE"}, f"memory-{ALICE}")]
    assert replies == [prompt.FORGOTTEN_TEXT]


def test_keep_it_on_changes_nothing(queued, replies):
    consent_store().decide(ALICE, GRANTED)
    slack_button_clicks.handler(click(prompt.KEEP), None)
    assert consent_store().get(ALICE).granted
    assert queued == []


@pytest.mark.parametrize("event", [click("something_else"), click(prompt.GRANT, kind="view_submission")])
def test_other_interactions_are_ignored(queued, replies, event):
    assert slack_button_clicks.handler(event, None)["statusCode"] == 200
    assert consent_store().get(ALICE) is None
    assert replies == []


def test_with_memory_off_a_click_is_acknowledged_and_nothing_else(monkeypatch, queued, replies):
    monkeypatch.delenv("USER_MEMORY_ENABLED")
    # A link button such as "Connect GitHub", then a memory button left over from before.
    assert slack_button_clicks.handler(click("connect_github"), None)["statusCode"] == 200
    assert slack_button_clicks.handler(click(prompt.GRANT), None)["statusCode"] == 200
    assert consent_store().get(ALICE) is None
    assert replies == [] and queued == []


def test_the_confirmation_only_goes_to_slack(monkeypatch):
    opened = []
    monkeypatch.setenv("SLACK_DRY_RUN", "false")
    monkeypatch.setattr(slack_button_clicks.urllib.request, "urlopen", lambda *a, **k: opened.append(a))
    slack_button_clicks._respond("https://evil.example.com/hook", "x")
    slack_button_clicks._respond(None, "x")
    assert opened == []
