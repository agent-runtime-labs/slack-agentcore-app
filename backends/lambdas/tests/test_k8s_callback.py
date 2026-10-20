"""Private login never activates without a signed, same-user Slack confirmation."""

import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlencode

import pytest

from conftest import SIGNING_SECRET
from slack_app.handlers import oauth_callback, slack_events
from slack_app.k8s_auth.settings import Settings
from slack_app.pending_auth import PendingAuth, pending_auth_store


@pytest.fixture
def configured(monkeypatch):
    settings = Settings("lab", "https://issuer.example/pinniped", "aud", "https://proxy.example", "",
        "jwt", "client.oauth.pinniped.dev-mitra", "secret", "table", "https://app.example/oauth2/callback")
    monkeypatch.setattr(Settings, "load", classmethod(lambda cls: settings))
    return settings


def pending(settings):
    return PendingAuth("nonce", "slack-T1-UA", "Kubernetes", "", "https://issuer.example/login", "C1", "UA", "1.0",
        int(time.time()) + 300, kubernetes={"state": "state", "nonce": "oidc-nonce", "fingerprint": settings.fingerprint},
        resume_job=json.dumps({"team_id": "T1", "user": "UA", "channel": "C1", "thread_ts": "1.0"}))


def callback_event(**params):
    return {"rawPath": "/oauth2/callback", "cookies": ["slack_agent_oauth=nonce"],
        "queryStringParameters": {"state": "state", "code": "authorization-code", **params}}


@pytest.mark.parametrize("params", [{"state": "wrong"}, {"iss": "https://evil.example"}, {"code": ""}])
def test_bad_callback_cannot_exchange_or_consume(configured, monkeypatch, params):
    from slack_app import k8s_callback
    protocol = Mock()
    monkeypatch.setattr(k8s_callback, "Protocol", protocol)
    pending_auth_store().put(pending(configured))
    assert oauth_callback.handler(callback_event(**params), None)["statusCode"] == 403
    protocol.assert_not_called()
    assert pending_auth_store().get("nonce") is not None


def test_browser_callback_only_stages_and_sends_private_confirmation(configured, monkeypatch):
    from slack_app import k8s_callback
    protocol = Mock()
    protocol.exchange_code.return_value = {"username": "Alice *untrusted*", "subject": "alice-sub"}
    store, slack = Mock(), Mock()
    store.stage.return_value = "private-confirmation-code"
    monkeypatch.setattr(k8s_callback, "Protocol", Mock(return_value=protocol))
    monkeypatch.setattr(k8s_callback, "Store", Mock(return_value=store))
    monkeypatch.setattr(k8s_callback, "slack_client", lambda: slack)
    pending_auth_store().put(pending(configured))
    result = oauth_callback.handler(callback_event(), None)
    assert result["statusCode"] == 200 and "Confirm in Slack" in result["body"]
    store.stage.assert_called_once()
    store.activate.assert_not_called()
    private = slack.chat_postEphemeral.call_args.kwargs
    assert private["user"] == "UA" and private["channel"] == "C1"
    assert private["blocks"][0]["text"]["type"] == "plain_text"
    assert private["blocks"][1]["elements"][0]["value"] == "private-confirmation-code"
    assert "private-confirmation-code" not in result["body"]
    assert oauth_callback.handler(callback_event(), None)["statusCode"] == 403


def interaction(team="T1", user="UA", valid_signature=True):
    payload = dict(type="block_actions", team={"id": team}, user={"id": user},
        actions=[{"action_id": "k8s_confirm", "value": "a" * 43}])
    body = urlencode({"payload": json.dumps(payload)})
    timestamp = str(int(time.time()))
    signature = hmac.new(SIGNING_SECRET.encode(), f"v0:{timestamp}:{body}".encode(), hashlib.sha256).hexdigest()
    return {"rawPath": "/slack/interactions", "body": body, "headers": {
        "x-slack-request-timestamp": timestamp, "x-slack-signature": "v0=" + (signature if valid_signature else "bad")}}


def test_unsigned_button_never_reads_vault(configured, monkeypatch):
    from slack_app import k8s_confirmation
    store = Mock()
    monkeypatch.setattr(k8s_confirmation, "Store", store)
    assert slack_events.handler(interaction(valid_signature=False), None)["statusCode"] == 401
    store.assert_not_called()


@pytest.mark.parametrize("team,user,allowed", [("T1", "UA", True), ("T2", "UA", False), ("T1", "UB", False)])
def test_signed_button_owner_binding_and_resume(configured, monkeypatch, team, user, allowed):
    from slack_app import k8s_confirmation
    row = {"resume_job": json.dumps({"team_id": "T1", "user": "UA", "channel": "C1", "thread_ts": "1.0"})}
    store, enqueue = Mock(), Mock()
    store.activate.side_effect = lambda owner, code: row if owner == "slack-T1-UA" else None
    monkeypatch.setattr(k8s_confirmation, "Store", Mock(return_value=store))
    monkeypatch.setattr(k8s_confirmation, "enqueue", enqueue)
    result = slack_events.handler(interaction(team, user), None)
    assert result["statusCode"] == 200
    assert enqueue.called == allowed
    store.activate.assert_called_once_with(f"slack-{team}-{user}", "a" * 43)
