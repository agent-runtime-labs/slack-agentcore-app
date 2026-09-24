import json
import time

import pytest
from conftest import mention_payload, signed_event

from slack_app.handlers import slack_events


@pytest.fixture
def queued(monkeypatch):
    jobs = []
    monkeypatch.setattr(slack_events, "enqueue", lambda job, group_id, dedup_id: jobs.append((job, group_id, dedup_id)))
    return jobs


@pytest.fixture
def reactions(monkeypatch):
    calls = []
    monkeypatch.setattr(slack_events, "add_reaction", lambda client, channel, ts, name: calls.append((channel, ts, name)))
    return calls


def test_rejects_bad_signature(queued):
    event = signed_event(mention_payload(), secret="wrong")
    assert slack_events.handler(event, None)["statusCode"] == 401
    assert queued == []


def test_rejects_stale_timestamp(queued):
    event = signed_event(mention_payload(), timestamp=int(time.time()) - 600)
    assert slack_events.handler(event, None)["statusCode"] == 401


def test_rejects_missing_headers(queued):
    event = signed_event(mention_payload())
    event["headers"] = {}
    assert slack_events.handler(event, None)["statusCode"] == 401


def test_url_verification_returns_challenge():
    result = slack_events.handler(signed_event({"type": "url_verification", "challenge": "abc"}), None)
    assert result["statusCode"] == 200
    assert json.loads(result["body"]) == {"challenge": "abc"}


def test_mention_is_queued_with_clean_text(queued):
    result = slack_events.handler(signed_event(mention_payload()), None)
    assert result["statusCode"] == 200
    job, group_id, dedup_id = queued[0]
    assert job["text"] == "what's my LinkedIn name?"
    assert job["user"] == "UALICE"
    assert job["team_id"] == "T999"
    assert job["thread_ts"] == "1726500000.000100"
    assert job["placeholder_ts"]
    assert job["user_message_ts"] == "1726500000.000100"
    assert group_id == "C123-1726500000.000100"
    assert dedup_id == "Ev1"


def test_mention_gets_a_working_reaction(queued, reactions):
    slack_events.handler(signed_event(mention_payload()), None)
    assert reactions == [("C123", "1726500000.000100", slack_events.REACTION_WORKING)]


def test_thread_reply_keeps_thread_ts(queued):
    slack_events.handler(signed_event(mention_payload(thread_ts="1726400000.000001")), None)
    assert queued[0][0]["thread_ts"] == "1726400000.000001"


@pytest.mark.parametrize(
    "overrides",
    [
        {"bot_id": "B1"},
        {"subtype": "message_changed"},
        {"user": None},
        {"type": "message", "channel_type": "channel"},
    ],
)
def test_ignores_non_user_events(queued, overrides):
    slack_events.handler(signed_event(mention_payload(**overrides)), None)
    assert queued == []


def test_direct_message_is_handled(queued):
    slack_events.handler(signed_event(mention_payload(text="hi", type="message", channel_type="im")), None)
    assert queued[0][0]["text"] == "hi"


def test_ignores_slack_retries(queued):
    event = signed_event(mention_payload(), extra_headers={"x-slack-retry-num": "1"})
    assert slack_events.handler(event, None)["statusCode"] == 200
    assert queued == []


def test_ignores_empty_mention(queued):
    slack_events.handler(signed_event(mention_payload(text="<@UBOT>")), None)
    assert queued == []


def app_home_payload(**event_overrides):
    event = {"type": "app_home_opened", "user": "UALICE", "tab": "home", "event_ts": "1726500000.000100"}
    event.update(event_overrides)
    return {"type": "event_callback", "team_id": "T999", "event_id": "Ev2", "event": event}


def test_app_home_opened_is_queued(queued):
    result = slack_events.handler(signed_event(app_home_payload()), None)
    assert result["statusCode"] == 200
    job, group_id, dedup_id = queued[0]
    assert job == {"type": "app_home", "team_id": "T999", "user": "UALICE"}
    assert group_id == "home-T999-UALICE"
    assert dedup_id == "Ev2"


def test_app_home_opened_ignores_non_home_tabs(queued):
    slack_events.handler(signed_event(app_home_payload(tab="messages")), None)
    assert queued == []
