import json
import time

import pytest

from slack_app.handlers import agent_worker
from slack_app.pending_auth import pending_auth_store

JOB = {
    "team_id": "T999",
    "channel": "C123",
    "user": "UALICE",
    "thread_ts": "1.1",
    "text": "what's my LinkedIn name?",
    "placeholder_ts": "1.2",
    "user_message_ts": "1.0",
}


class RecordingSlack:
    def __init__(self):
        self.calls = []

    def chat_update(self, **kwargs):
        self.calls.append(("update", kwargs))

    def chat_postEphemeral(self, **kwargs):  # noqa: N802
        self.calls.append(("ephemeral", kwargs))

    def reactions_add(self, **kwargs):
        self.calls.append(("reaction_add", kwargs))

    def reactions_remove(self, **kwargs):
        self.calls.append(("reaction_remove", kwargs))


def _messages(calls):
    """Non-reaction calls, for tests that only care about the message content."""
    return [call for call in calls if call[0] not in ("reaction_add", "reaction_remove")]


@pytest.fixture
def slack(monkeypatch):
    client = RecordingSlack()
    monkeypatch.setattr(agent_worker, "slack_client", lambda: client)
    return client


def test_answer_replaces_placeholder(monkeypatch, slack):
    seen = {}

    def fake_invoke(prompt, user_id, session_id, channel, message_ts):
        seen.update(prompt=prompt, user_id=user_id, session_id=session_id, channel=channel, message_ts=message_ts)
        return {"message": "Your name is Alice.", "authRequired": None}

    monkeypatch.setattr(agent_worker, "invoke_agent", fake_invoke)
    agent_worker.process(JOB)

    assert seen["user_id"] == "slack-T999-UALICE"
    assert len(seen["session_id"]) == 64
    assert seen["channel"] == "C123"
    assert seen["message_ts"] == "1.2"
    assert _messages(slack.calls) == [("update", {"channel": "C123", "ts": "1.2", "text": "Your name is Alice."})]


def test_auth_required_sends_private_link(monkeypatch, slack):
    monkeypatch.setattr(
        agent_worker,
        "invoke_agent",
        lambda *a: {
            "message": "x",
            "authRequired": {"provider": "LinkedIn", "authorizationUrl": "https://li/auth", "sessionUri": "urn:s1"},
        },
    )
    agent_worker.process(JOB)

    messages = _messages(slack.calls)
    kinds = [kind for kind, _ in messages]
    assert kinds == ["ephemeral", "update"]
    ephemeral = messages[0][1]
    assert ephemeral["user"] == "UALICE"
    link = ephemeral["blocks"][0]["accessory"]["url"]
    assert link.startswith("http://localhost:8081/oauth2/start?nonce=")
    # The raw LinkedIn URL is never shown in Slack; it lives in the pending record.
    assert "https://li/auth" not in str(slack.calls)

    nonce = link.split("nonce=")[1]
    pending = pending_auth_store().get(nonce)
    assert pending.runtime_user_id == "slack-T999-UALICE"
    assert pending.session_uri == "urn:s1"
    assert "LinkedIn" in messages[1][1]["text"]


def test_slow_agent_call_gets_interim_update(monkeypatch, slack):
    monkeypatch.setattr(agent_worker, "INTERIM_DELAY_SECONDS", 0.05)

    def slow_invoke(prompt, user_id, session_id, channel, message_ts):
        time.sleep(0.2)
        return {"message": "Your name is Alice.", "authRequired": None}

    monkeypatch.setattr(agent_worker, "invoke_agent", slow_invoke)
    agent_worker.process(JOB)

    assert _messages(slack.calls) == [
        ("update", {"channel": "C123", "ts": "1.2", "text": agent_worker.INTERIM_TEXT}),
        ("update", {"channel": "C123", "ts": "1.2", "text": "Your name is Alice."}),
    ]


def test_fast_agent_call_gets_no_interim_update(monkeypatch, slack):
    monkeypatch.setattr(agent_worker, "INTERIM_DELAY_SECONDS", 5)
    monkeypatch.setattr(agent_worker, "invoke_agent", lambda *a: {"message": "fast", "authRequired": None})

    agent_worker.process(JOB)
    time.sleep(0.1)  # long enough for a wrongly-firing timer to show up, well under the 5s delay

    assert _messages(slack.calls) == [("update", {"channel": "C123", "ts": "1.2", "text": "fast"})]


def test_agent_failure_is_reported_not_raised(monkeypatch, slack):
    def boom(*a):
        raise RuntimeError("runtime down")

    monkeypatch.setattr(agent_worker, "invoke_agent", boom)
    agent_worker.handler({"Records": [{"body": json.dumps(JOB)}]}, None)
    messages = _messages(slack.calls)
    assert messages[0][0] == "update"
    assert "went wrong" in messages[0][1]["text"]


def _reactions(calls):
    return [(kind, kwargs["name"]) for kind, kwargs in calls if kind in ("reaction_add", "reaction_remove")]


def test_successful_reply_swaps_hourglass_for_check_mark(monkeypatch, slack):
    monkeypatch.setattr(agent_worker, "invoke_agent", lambda *a: {"message": "hi", "authRequired": None})
    agent_worker.process(JOB)

    assert _reactions(slack.calls) == [
        ("reaction_remove", agent_worker.REACTION_WORKING),
        ("reaction_add", agent_worker.REACTION_DONE),
    ]
    for kind, kwargs in slack.calls:
        if kind in ("reaction_add", "reaction_remove"):
            assert kwargs["channel"] == "C123"
            assert kwargs["timestamp"] == "1.0"


def test_auth_required_swaps_hourglass_for_lock(monkeypatch, slack):
    monkeypatch.setattr(
        agent_worker,
        "invoke_agent",
        lambda *a: {"message": "x", "authRequired": {"provider": "LinkedIn", "authorizationUrl": "https://li/auth"}},
    )
    agent_worker.process(JOB)

    assert _reactions(slack.calls) == [
        ("reaction_remove", agent_worker.REACTION_WORKING),
        ("reaction_add", agent_worker.REACTION_AUTH_REQUIRED),
    ]


def test_failure_swaps_hourglass_for_warning(monkeypatch, slack):
    monkeypatch.setattr(agent_worker, "invoke_agent", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    agent_worker.process(JOB)

    assert _reactions(slack.calls) == [
        ("reaction_remove", agent_worker.REACTION_WORKING),
        ("reaction_add", agent_worker.REACTION_ERROR),
    ]


def test_no_reaction_swap_without_user_message_ts(monkeypatch, slack):
    job = {k: v for k, v in JOB.items() if k != "user_message_ts"}
    monkeypatch.setattr(agent_worker, "invoke_agent", lambda *a: {"message": "hi", "authRequired": None})
    agent_worker.process(job)

    assert _reactions(slack.calls) == []


def test_a_failed_reaction_call_does_not_break_the_reply(monkeypatch, slack):
    def boom_reaction(**kwargs):
        raise RuntimeError("missing scope")

    slack.reactions_add = boom_reaction
    slack.reactions_remove = boom_reaction
    monkeypatch.setattr(agent_worker, "invoke_agent", lambda *a: {"message": "hi", "authRequired": None})

    agent_worker.process(JOB)  # must not raise
    assert _messages(slack.calls) == [("update", {"channel": "C123", "ts": "1.2", "text": "hi"})]


def test_app_home_job_is_routed_to_app_home_not_the_chat_flow(monkeypatch, slack):
    calls = []
    monkeypatch.setattr(agent_worker.app_home, "publish_app_home", lambda team_id, user: calls.append((team_id, user)))
    monkeypatch.setattr(agent_worker, "invoke_agent", lambda *a: (_ for _ in ()).throw(AssertionError("chat flow ran")))

    job = {"type": "app_home", "team_id": "T999", "user": "UALICE"}
    agent_worker.handler({"Records": [{"body": json.dumps(job)}]}, None)

    assert calls == [("T999", "UALICE")]
    assert slack.calls == []
