import pytest
from conftest import channel_message_payload, mention_payload, signed_event

from slack_app.handlers import slack_events
from slack_app.knowledge import CHECK, DELETE_CHANNEL, DELETE_THREAD, events

THREAD_TS = "1726400000.000001"


@pytest.fixture
def queued(monkeypatch):
    jobs = []
    monkeypatch.setattr(slack_events, "enqueue", lambda job, group_id, dedup_id: jobs.append(job))
    return jobs


@pytest.fixture
def knowledge(monkeypatch):
    """Knowledge jobs queued, as (job, delay)."""
    monkeypatch.setenv("KNOWLEDGE_ENABLED", "true")
    jobs = []
    monkeypatch.setattr(events, "enqueue_knowledge", lambda job, delay: jobs.append((job, delay)))
    return jobs


def _send(payload):
    return slack_events.handler(signed_event(payload), None)


def _event(payload_type, **fields):
    return {"type": "event_callback", "team_id": "T999", "event_id": "Ev9", "event": {"type": payload_type, **fields}}


def test_disabled_by_default_nothing_is_queued_and_answering_is_unchanged(monkeypatch, queued):
    jobs = []
    monkeypatch.setattr(events, "enqueue_knowledge", lambda job, delay: jobs.append(job))
    _send(channel_message_payload(thread_ts=THREAD_TS))
    assert jobs == []
    assert queued[0]["triage"]


def test_a_reply_in_a_thread_queues_a_delayed_check_without_its_text(queued, knowledge):
    _send(channel_message_payload(text="the rollback fixed it", thread_ts=THREAD_TS, ts="1726400100.000001"))

    ((job, delay),) = knowledge
    assert job == {"team_id": "T999", "channel": "C123", "kind": CHECK, "thread_ts": THREAD_TS,
                   "trigger_ts": "1726400100.000001", "force": False}  # fmt: skip
    assert delay == 600
    assert queued[0]["triage"]  # the answer path is unaffected


def test_a_top_level_message_queues_a_check_for_its_future_thread(queued, knowledge):
    # The indexer skips it if nobody replies; if the bot answers, this is the only trigger.
    _send(channel_message_payload())
    ((job, _),) = knowledge
    assert job["thread_ts"] == job["trigger_ts"] == "1726500000.000100"


def test_the_app_mention_copy_does_not_queue_a_second_check(queued, knowledge):
    _send(mention_payload(thread_ts=THREAD_TS))
    assert knowledge == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"bot_id": "B1", "user": "UBOT"},  # bots, including this one
        {"channel_type": "im"},
        {"channel_type": "mpim"},
        {"subtype": "channel_join"},
    ],
)
def test_nothing_is_queued_for_bots_dms_or_joins(queued, knowledge, overrides):
    _send(channel_message_payload(thread_ts=THREAD_TS, **overrides))
    assert knowledge == []


def test_slack_connect_channels_are_never_queued(queued, knowledge):
    payload = channel_message_payload(thread_ts=THREAD_TS)
    payload["is_ext_shared_channel"] = True
    _send(payload)
    assert knowledge == []


def test_excluded_channels_are_never_queued(monkeypatch, queued, knowledge):
    monkeypatch.setenv("KNOWLEDGE_EXCLUDED_CHANNELS", "C999, C123")
    _send(channel_message_payload(thread_ts=THREAD_TS))
    assert knowledge == []


def _edit(message, previous, **fields):
    return _event("message", channel="C123", channel_type="channel", subtype="message_changed",
                  event_ts="1726400500.000001", message=message, previous_message=previous, **fields)  # fmt: skip


def test_an_edit_forces_a_full_re_summary(queued, knowledge):
    before = {"user": "UALICE", "text": "it was ECR", "ts": "1726400100.000001", "thread_ts": THREAD_TS}
    _send(_edit({**before, "text": "it was ECR throttling"}, before))

    ((job, delay),) = knowledge
    assert job["kind"] == CHECK and job["force"] is True
    assert job["trigger_ts"] == "1726400500.000001"
    assert delay == 600


@pytest.mark.parametrize(
    "message",
    [
        # An unfurl or reply count changing: same text.
        {"user": "UALICE", "text": "it was ECR", "ts": "1.1", "thread_ts": THREAD_TS},
        # The bot updating its own placeholder with progress.
        {"user": "UBOT", "bot_id": "B1", "text": "🔎 Reading…", "ts": "1.1", "thread_ts": THREAD_TS},
        # A message with no replies isn't a thread yet.
        {"user": "UALICE", "text": "new text", "ts": "1.1"},
    ],
)
def test_edits_that_are_not_people_changing_a_thread_are_ignored(queued, knowledge, message):
    _send(_edit(message, {"user": "UALICE", "text": "it was ECR", "ts": "1.1", "thread_ts": THREAD_TS}))
    assert knowledge == []


def test_deleting_a_first_message_with_replies_deletes_the_thread_now(queued, knowledge):
    tombstone = {"subtype": "tombstone", "text": "This message was deleted.", "ts": THREAD_TS, "thread_ts": THREAD_TS}
    _send(_edit(tombstone, {"user": "UALICE", "text": "staging is down", "ts": THREAD_TS, "thread_ts": THREAD_TS}))

    ((job, delay),) = knowledge
    assert job == {"team_id": "T999", "channel": "C123", "kind": DELETE_THREAD, "thread_ts": THREAD_TS}
    assert delay == 0


def _deletion(deleted_ts, previous):
    return _event("message", channel="C123", channel_type="channel", subtype="message_deleted",
                  deleted_ts=deleted_ts, event_ts="1726400600.000001", previous_message=previous)  # fmt: skip


def test_deleting_a_reply_re_summarises_without_it(queued, knowledge):
    _send(_deletion("1726400100.000001", {"user": "UBOB", "ts": "1726400100.000001", "thread_ts": THREAD_TS}))
    ((job, _),) = knowledge
    assert (job["kind"], job["force"], job["thread_ts"]) == (CHECK, True, THREAD_TS)


def test_deleting_a_first_message_deletes_the_thread(queued, knowledge):
    _send(_deletion(THREAD_TS, {"user": "UALICE", "ts": THREAD_TS, "thread_ts": THREAD_TS}))
    ((job, delay),) = knowledge
    assert (job["kind"], delay) == (DELETE_THREAD, 0)


def test_deleting_a_message_without_replies_does_nothing(queued, knowledge):
    _send(_deletion("1.5", {"user": "UALICE", "ts": "1.5"}))
    assert knowledge == []


@pytest.mark.parametrize("event_type", ["channel_left", "group_left"])
def test_the_bot_leaving_a_channel_deletes_the_channel_now(queued, knowledge, event_type):
    _send(_event(event_type, channel="C123", actor_id="UADMIN"))

    ((job, delay),) = knowledge
    assert job == {"team_id": "T999", "channel": "C123", "kind": DELETE_CHANNEL}
    assert delay == 0
    assert queued == []


def test_a_failure_to_queue_never_touches_the_answer(monkeypatch, queued):
    monkeypatch.setenv("KNOWLEDGE_ENABLED", "true")

    def broken(job, delay):
        raise RuntimeError("SQS is down")

    monkeypatch.setattr(events, "enqueue_knowledge", broken)
    result = _send(mention_payload())
    assert result["statusCode"] == 200
    assert queued[0]["placeholder_ts"]


def test_quiet_period_can_be_shortened_but_not_past_the_sqs_limit(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_QUIET_SECONDS", "5")
    assert events.quiet_seconds() == 5
    monkeypatch.setenv("KNOWLEDGE_QUIET_SECONDS", "3600")
    assert events.quiet_seconds() == 900
