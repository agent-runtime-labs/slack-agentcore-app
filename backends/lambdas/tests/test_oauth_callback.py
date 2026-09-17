import time

import pytest

from slack_app.handlers import oauth_callback
from slack_app.pending_auth import PendingAuth, pending_auth_store


class FakeIdentity:
    def __init__(self):
        self.calls = []

    def complete_resource_token_auth(self, **kwargs):
        self.calls.append(kwargs)


@pytest.fixture
def identity(monkeypatch):
    fake = FakeIdentity()
    monkeypatch.setattr(oauth_callback, "_identity", lambda: fake)
    return fake


@pytest.fixture
def pending():
    item = PendingAuth(
        nonce="n1",
        runtime_user_id="slack-T999-UALICE",
        session_uri="urn:session:abc",
        authorization_url="https://www.linkedin.com/oauth/v2/authorization?x=1",
        channel="C123",
        slack_user="UALICE",
        thread_ts="1.1",
        expires_at=int(time.time()) + 600,
    )
    pending_auth_store().put(item)
    return item


def event(path, query=None, cookies=None):
    return {"rawPath": path, "queryStringParameters": query, "cookies": cookies or []}


def test_start_sets_cookie_and_redirects(pending):
    result = oauth_callback.handler(event("/oauth2/start", {"nonce": "n1"}), None)
    assert result["statusCode"] == 302
    assert result["headers"]["Location"] == pending.authorization_url
    cookie = result["cookies"][0]
    assert cookie.startswith("slack_agent_oauth=n1;")
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Path=/oauth2" in cookie


def test_start_rejects_unknown_or_expired_nonce(pending):
    assert oauth_callback.handler(event("/oauth2/start", {"nonce": "nope"}), None)["statusCode"] == 400
    pending_auth_store().put(PendingAuth(**{**pending.__dict__, "nonce": "old", "expires_at": 1}))
    assert oauth_callback.handler(event("/oauth2/start", {"nonce": "old"}), None)["statusCode"] == 400


def test_callback_completes_for_matching_user(pending, identity):
    result = oauth_callback.handler(
        event("/oauth2/callback", {"session_id": "urn:session:abc"}, ["slack_agent_oauth=n1"]), None
    )
    assert result["statusCode"] == 200
    assert identity.calls == [{"sessionUri": "urn:session:abc", "userIdentifier": {"userId": "slack-T999-UALICE"}}]
    assert "Max-Age=0" in result["cookies"][0]
    # Single use.
    assert pending_auth_store().get("n1") is None


def test_callback_without_cookie_is_rejected(pending, identity):
    result = oauth_callback.handler(event("/oauth2/callback", {"session_id": "urn:session:abc"}), None)
    assert result["statusCode"] == 400
    assert identity.calls == []


def test_callback_with_other_session_is_rejected(pending, identity):
    # e.g. an attacker's consent session replayed into the victim's browser
    result = oauth_callback.handler(
        event("/oauth2/callback", {"session_id": "urn:session:attacker"}, ["slack_agent_oauth=n1"]), None
    )
    assert result["statusCode"] == 403
    assert identity.calls == []
    assert pending_auth_store().get("n1") is not None


def test_callback_replay_is_rejected(pending, identity):
    ok = event("/oauth2/callback", {"session_id": "urn:session:abc"}, ["slack_agent_oauth=n1"])
    assert oauth_callback.handler(ok, None)["statusCode"] == 200
    assert oauth_callback.handler(ok, None)["statusCode"] == 403
    assert len(identity.calls) == 1


def test_identity_failure_returns_error_page(pending, monkeypatch):
    class Failing:
        def complete_resource_token_auth(self, **kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(oauth_callback, "_identity", lambda: Failing())
    result = oauth_callback.handler(
        event("/oauth2/callback", {"session_id": "urn:session:abc"}, ["slack_agent_oauth=n1"]), None
    )
    assert result["statusCode"] == 502


def test_unknown_path_is_404():
    assert oauth_callback.handler(event("/oauth2/other"), None)["statusCode"] == 404
