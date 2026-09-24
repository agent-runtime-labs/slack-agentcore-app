import pytest

from slack_app import app_home
from slack_app.pending_auth import pending_auth_store


class RecordingSlack:
    def __init__(self):
        self.published = []

    def views_publish(self, **kwargs):
        self.published.append(kwargs)


@pytest.fixture
def slack(monkeypatch):
    client = RecordingSlack()
    monkeypatch.setattr(app_home, "slack_client", lambda: client)
    return client


def test_connected_provider_has_no_button(monkeypatch, slack):
    monkeypatch.setattr(
        app_home.agent_client,
        "check_connections",
        lambda user_id: {"connections": {"providers": [{"key": "github", "displayName": "GitHub", "connected": True}]}},
    )

    app_home.publish_app_home("T999", "UALICE")

    view = slack.published[0]
    assert view["user_id"] == "UALICE"
    section = view["view"]["blocks"][-1]
    assert "Connected" in section["text"]["text"]
    assert "accessory" not in section


def test_disconnected_identity_provider_gets_a_connect_button(monkeypatch, slack):
    monkeypatch.setattr(
        app_home.agent_client,
        "check_connections",
        lambda user_id: {
            "connections": {
                "providers": [
                    {
                        "key": "linkedin",
                        "displayName": "LinkedIn",
                        "connected": False,
                        "authorizationUrl": "https://li/auth",
                        "sessionUri": "urn:s1",
                    }
                ]
            }
        },
    )

    app_home.publish_app_home("T999", "UALICE")

    section = slack.published[0]["view"]["blocks"][-1]
    link = section["accessory"]["url"]
    assert link.startswith("http://localhost:8081/oauth2/start?nonce=")

    nonce = link.split("nonce=")[1]
    pending = pending_auth_store().get(nonce)
    assert pending.runtime_user_id == "slack-T999-UALICE"
    assert pending.slack_user == "UALICE"
    assert pending.team_id == "T999"
    assert pending.session_uri == "urn:s1"
    assert pending.channel == ""


def test_disconnected_cimd_provider_carries_its_payload(monkeypatch, slack):
    monkeypatch.setattr(
        app_home.agent_client,
        "check_connections",
        lambda user_id: {
            "connections": {
                "providers": [
                    {
                        "key": "linear",
                        "displayName": "Linear",
                        "connected": False,
                        "authorizationUrl": "https://linear/auth",
                        "cimd": {"provider": "linear", "state": "s1"},
                    }
                ]
            }
        },
    )

    app_home.publish_app_home("T999", "UALICE")

    link = slack.published[0]["view"]["blocks"][-1]["accessory"]["url"]
    nonce = link.split("nonce=")[1]
    pending = pending_auth_store().get(nonce)
    assert pending.cimd == {"provider": "linear", "state": "s1"}
    assert pending.session_uri == ""


def test_no_providers_configured(monkeypatch, slack):
    monkeypatch.setattr(app_home.agent_client, "check_connections", lambda user_id: {"connections": {"providers": []}})

    app_home.publish_app_home("T999", "UALICE")

    blocks = slack.published[0]["view"]["blocks"]
    assert "No providers" in blocks[-1]["text"]["text"]


def test_agent_failure_publishes_an_error_view(monkeypatch, slack):
    def boom(user_id):
        raise RuntimeError("runtime down")

    monkeypatch.setattr(app_home.agent_client, "check_connections", boom)

    app_home.publish_app_home("T999", "UALICE")

    text = slack.published[0]["view"]["blocks"][0]["text"]["text"]
    assert "Couldn't load" in text


def test_a_failed_publish_does_not_raise(monkeypatch):
    class BoomSlack:
        def views_publish(self, **kwargs):
            raise RuntimeError("missing scope")

    monkeypatch.setattr(app_home, "slack_client", lambda: BoomSlack())
    monkeypatch.setattr(
        app_home.agent_client, "check_connections", lambda user_id: {"connections": {"providers": []}}
    )

    app_home.publish_app_home("T999", "UALICE")  # must not raise
