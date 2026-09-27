import json
import time

import pytest
from conftest import channel_message_payload, mention_payload, signed_event

from slack_app import slack
from slack_app.engaged_threads import is_engaged, mark_engaged
from slack_app.handlers import slack_events


@pytest.fixture
def queued(monkeypatch):
    jobs = []
    monkeypatch.setattr(slack_events, "enqueue", lambda job, group_id, dedup_id: jobs.append((job, group_id, dedup_id)))
    return jobs


@pytest.fixture
def reactions(monkeypatch):
    calls = []
    monkeypatch.setattr(slack, "add_reaction", lambda client, channel, ts, name: calls.append((channel, ts, name)))
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
    assert reactions == [("C123", "1726500000.000100", slack.REACTION_WORKING)]


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


def test_mention_marks_the_thread_as_engaged(queued):
    slack_events.handler(signed_event(mention_payload(thread_ts="1726400000.000001")), None)
    assert is_engaged("T999", "C123", "1726400000.000001")


def test_mention_in_a_channel_is_not_handled_twice(queued):
    # Slack delivers an @mention both as app_mention and as a plain channel message.
    slack_events.handler(signed_event(channel_message_payload(text="<@UBOT> hi")), None)
    assert queued == []


def test_top_level_channel_message_is_queued_for_triage_silently(queued, reactions):
    slack_events.handler(signed_event(channel_message_payload()), None)

    job = queued[0][0]
    assert job["triage"] == {"text": "does anyone know how to connect Notion?", "bot_in_thread": False}
    assert "placeholder_ts" not in job
    assert reactions == []


def test_follow_up_in_an_engaged_thread_is_triaged_with_that_context(queued, reactions):
    mark_engaged("T999", "C123", "1726400000.000001")
    slack_events.handler(signed_event(channel_message_payload(text="and the oldest one?", thread_ts="1726400000.000001")), None)

    job = queued[0][0]
    assert job["triage"] == {"text": "and the oldest one?", "bot_in_thread": True}
    assert "placeholder_ts" not in job
    assert reactions == []


def test_mention_then_follow_up_without_mention(queued):
    slack_events.handler(signed_event(mention_payload(thread_ts="1726400000.000001")), None)
    follow_up = channel_message_payload(text="thanks, and my GitHub?", thread_ts="1726400000.000001", ts="1726500001.0")
    slack_events.handler(signed_event(follow_up), None)

    assert "triage" not in queued[0][0]
    assert queued[1][0]["triage"] == {"text": "thanks, and my GitHub?", "bot_in_thread": True}


def test_follow_up_naming_a_person_in_plain_text_is_not_answered_up_front(queued, reactions):
    # "Philip" isn't a Slack @mention, so only triage can tell this is meant for a person.
    mark_engaged("T999", "C123", "1726400000.000001")
    text = "Philip can you confirm do you have access to these repo"
    slack_events.handler(signed_event(channel_message_payload(text=text, thread_ts="1726400000.000001")), None)

    assert queued[0][0]["triage"] == {"text": text, "bot_in_thread": True}
    assert reactions == []


def test_follow_up_addressed_to_someone_else_goes_to_triage(queued, reactions):
    mark_engaged("T999", "C123", "1726400000.000001")
    text = "<@UBOB> can you take LIN-12?"
    slack_events.handler(signed_event(channel_message_payload(text=text, thread_ts="1726400000.000001")), None)

    assert queued[0][0]["triage"] == {"text": text, "bot_in_thread": True}
    assert reactions == []


def test_reply_in_a_thread_the_bot_is_not_in_goes_to_triage(queued):
    slack_events.handler(signed_event(channel_message_payload(thread_ts="1726400000.000009")), None)
    assert queued[0][0]["triage"]["bot_in_thread"] is False


def test_private_channel_messages_are_handled_too(queued):
    slack_events.handler(signed_event(channel_message_payload(channel_type="group")), None)
    assert queued[0][0]["triage"]


def test_bot_user_id_falls_back_to_auth_test(queued):
    payload = channel_message_payload(text="<@UBOTLOCAL> hi")  # the dry-run client's auth.test user
    del payload["authorizations"]
    slack_events.handler(signed_event(payload), None)
    assert queued == []


PDF = {"id": "F1", "name": "invoice-sept.pdf", "mimetype": "application/pdf", "size": 52_000, "url_private": "u"}


def test_file_with_no_text_in_a_dm_is_handled(queued):
    payload = mention_payload(text="", type="message", channel_type="im", subtype="file_share", files=[PDF])
    slack_events.handler(signed_event(payload), None)

    job = queued[0][0]
    assert job["text"] == ""
    assert job["files"] == [{"id": "F1", "name": "invoice-sept.pdf", "mimetype": "application/pdf", "size": 52_000}]
    assert job["placeholder_ts"]


def test_mention_with_a_file_carries_the_file(queued):
    slack_events.handler(signed_event(mention_payload(text="<@UBOT> why is this failing?", files=[PDF])), None)
    job = queued[0][0]
    assert job["text"] == "why is this failing?"
    assert [file["id"] for file in job["files"]] == ["F1"]


def test_file_shared_in_a_channel_goes_to_triage(queued, reactions):
    payload = channel_message_payload(text="Here's the Q3 export", subtype="file_share", files=[PDF])
    slack_events.handler(signed_event(payload), None)

    job = queued[0][0]
    assert job["triage"] == {"text": "Here's the Q3 export", "bot_in_thread": False}
    assert job["files"][0]["name"] == "invoice-sept.pdf"
    assert reactions == []


def test_messages_without_files_have_no_files_key(queued):
    slack_events.handler(signed_event(mention_payload()), None)
    assert "files" not in queued[0][0]


def test_deleted_file_with_no_text_is_ignored(queued):
    gone = {"id": "F1", "name": "x.pdf", "mode": "tombstone"}
    payload = mention_payload(text="", type="message", channel_type="im", subtype="file_share", files=[gone])
    slack_events.handler(signed_event(payload), None)
    assert queued == []
