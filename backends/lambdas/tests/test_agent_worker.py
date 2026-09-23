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
}


class RecordingSlack:
    def __init__(self):
        self.calls = []

    def chat_update(self, **kwargs):
        self.calls.append(("update", kwargs))

    def chat_postEphemeral(self, **kwargs):  # noqa: N802
        self.calls.append(("ephemeral", kwargs))


@pytest.fixture
def slack(monkeypatch):
    client = RecordingSlack()
    monkeypatch.setattr(agent_worker, "slack_client", lambda: client)
    return client


def test_answer_replaces_placeholder(monkeypatch, slack):
    seen = {}

    def fake_invoke(prompt, user_id, session_id):
        seen.update(prompt=prompt, user_id=user_id, session_id=session_id)
        return {"message": "Your name is Alice.", "authRequired": None}

    monkeypatch.setattr(agent_worker, "invoke_agent", fake_invoke)
    agent_worker.process(JOB)

    assert seen["user_id"] == "slack-T999-UALICE"
    assert len(seen["session_id"]) == 64
    assert slack.calls == [("update", {"channel": "C123", "ts": "1.2", "text": "Your name is Alice."})]


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

    kinds = [kind for kind, _ in slack.calls]
    assert kinds == ["ephemeral", "update"]
    ephemeral = slack.calls[0][1]
    assert ephemeral["user"] == "UALICE"
    link = ephemeral["blocks"][0]["accessory"]["url"]
    assert link.startswith("http://localhost:8081/oauth2/start?nonce=")
    # The raw LinkedIn URL is never shown in Slack; it lives in the pending record.
    assert "https://li/auth" not in str(slack.calls)

    nonce = link.split("nonce=")[1]
    pending = pending_auth_store().get(nonce)
    assert pending.runtime_user_id == "slack-T999-UALICE"
    assert pending.session_uri == "urn:s1"
    assert "LinkedIn" in slack.calls[1][1]["text"]


def test_slow_agent_call_gets_interim_update(monkeypatch, slack):
    monkeypatch.setattr(agent_worker, "INTERIM_DELAY_SECONDS", 0.05)

    def slow_invoke(prompt, user_id, session_id):
        time.sleep(0.2)
        return {"message": "Your name is Alice.", "authRequired": None}

    monkeypatch.setattr(agent_worker, "invoke_agent", slow_invoke)
    agent_worker.process(JOB)

    assert slack.calls == [
        ("update", {"channel": "C123", "ts": "1.2", "text": agent_worker.INTERIM_TEXT}),
        ("update", {"channel": "C123", "ts": "1.2", "text": "Your name is Alice."}),
    ]


def test_fast_agent_call_gets_no_interim_update(monkeypatch, slack):
    monkeypatch.setattr(agent_worker, "INTERIM_DELAY_SECONDS", 5)
    monkeypatch.setattr(agent_worker, "invoke_agent", lambda *a: {"message": "fast", "authRequired": None})

    agent_worker.process(JOB)
    time.sleep(0.1)  # long enough for a wrongly-firing timer to show up, well under the 5s delay

    assert slack.calls == [("update", {"channel": "C123", "ts": "1.2", "text": "fast"})]


def test_agent_failure_is_reported_not_raised(monkeypatch, slack):
    def boom(*a):
        raise RuntimeError("runtime down")

    monkeypatch.setattr(agent_worker, "invoke_agent", boom)
    agent_worker.handler({"Records": [{"body": __import__("json").dumps(JOB)}]}, None)
    assert slack.calls[0][0] == "update"
    assert "went wrong" in slack.calls[0][1]["text"]
