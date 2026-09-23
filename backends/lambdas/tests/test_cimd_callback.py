"""The CIMD half of /oauth2/callback, plus the client metadata document we publish.

The AgentCore Identity half is covered by test_oauth_callback.py; both share the cookie
nonce binding, so what is tested here is what CIMD adds: the `state` check, the `iss`
check, and the single-use code exchange.
"""

import json
import time

import pytest

from slack_app import cimd_client
from slack_app.handlers import oauth_callback
from slack_app.pending_auth import PendingAuth, pending_auth_store

CIMD_PAYLOAD = {
    "provider": "linear",
    "displayName": "Linear",
    "state": "state-abc",
    "codeVerifier": "verifier-xyz",
    "clientId": "http://localhost:8081/oauth2/client-metadata.json",
    "redirectUri": "http://localhost:8081/oauth2/callback",
    "tokenEndpoint": "https://mcp.linear.app/token",
    "issuer": "https://mcp.linear.app",
    "resource": "https://mcp.linear.app/mcp",
    "scope": "read write",
}


@pytest.fixture
def exchange(monkeypatch):
    """Stands in for the provider's token endpoint and the token table."""
    calls = {"exchange": [], "stored": []}
    response = {"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600, "scope": "read write"}

    def fake_exchange(cimd, code):
        calls["exchange"].append((cimd["provider"], code, cimd["codeVerifier"]))
        return response

    def fake_store(user_id, cimd, tokens):
        calls["stored"].append((user_id, cimd["provider"], tokens["access_token"]))

    monkeypatch.setattr(oauth_callback.cimd_client, "exchange_code", fake_exchange)
    monkeypatch.setattr(oauth_callback.cimd_tokens, "store_tokens", fake_store)
    return calls


@pytest.fixture
def pending():
    item = PendingAuth(
        nonce="n1",
        runtime_user_id="slack-T999-UALICE",
        provider="Linear",
        session_uri="",
        authorization_url="https://mcp.linear.app/authorize?x=1",
        channel="C123",
        slack_user="UALICE",
        thread_ts="1.1",
        expires_at=int(time.time()) + 600,
        cimd=dict(CIMD_PAYLOAD),
    )
    pending_auth_store().put(item)
    return item


def event(path, query=None, cookies=None):
    return {"rawPath": path, "queryStringParameters": query, "cookies": cookies or []}


def callback(query, cookies=("slack_agent_oauth=n1",)):
    return oauth_callback.handler(event("/oauth2/callback", query, list(cookies)), None)


# --- the client metadata document ---------------------------------------------------


def test_serves_the_client_metadata_document():
    result = oauth_callback.handler(event("/oauth2/client-metadata.json"), None)
    document = json.loads(result["body"])

    assert result["statusCode"] == 200
    assert result["headers"]["Cache-Control"] == cimd_client.CACHE_CONTROL
    # The client_id must be the URL the document is served from: authorization servers
    # check it, and it is what makes a copied document useless to somebody else.
    assert document["client_id"] == "http://localhost:8081/oauth2/client-metadata.json"
    assert document["redirect_uris"] == ["http://localhost:8081/oauth2/callback"]
    # A CIMD client is public by definition -- the draft forbids shared secrets.
    assert document["token_endpoint_auth_method"] == "none"
    assert "client_secret" not in document
    assert len(result["body"]) < 5000  # the draft recommends staying under ~5 KB


# --- completing consent -------------------------------------------------------------


def test_exchanges_the_code_and_stores_tokens(pending, exchange):
    result = callback({"code": "code-1", "state": "state-abc", "iss": "https://mcp.linear.app"})

    assert result["statusCode"] == 200
    assert exchange["exchange"] == [("linear", "code-1", "verifier-xyz")]
    assert exchange["stored"] == [("slack-T999-UALICE", "linear", "at-1")]
    assert "Max-Age=0" in result["cookies"][0]
    assert pending_auth_store().get("n1") is None  # single use


def test_missing_iss_is_accepted(pending, exchange):
    # RFC 9207 is optional; only a *wrong* issuer is a problem.
    assert callback({"code": "code-1", "state": "state-abc"})["statusCode"] == 200
    assert len(exchange["exchange"]) == 1


def test_wrong_state_is_rejected(pending, exchange):
    result = callback({"code": "code-1", "state": "state-of-someone-else"})

    assert result["statusCode"] == 403
    assert exchange["exchange"] == []
    assert pending_auth_store().get("n1") is not None  # still usable by the real user


def test_wrong_issuer_is_rejected(pending, exchange):
    result = callback({"code": "code-1", "state": "state-abc", "iss": "https://evil.example.com"})

    assert result["statusCode"] == 403
    assert exchange["exchange"] == []


def test_missing_code_is_rejected(pending, exchange):
    assert callback({"state": "state-abc"})["statusCode"] == 403
    assert exchange["exchange"] == []


def test_replay_is_rejected(pending, exchange):
    query = {"code": "code-1", "state": "state-abc"}
    assert callback(query)["statusCode"] == 200
    assert callback(query)["statusCode"] == 403
    assert len(exchange["exchange"]) == 1


def test_user_declining_consent_is_not_an_error(pending, exchange):
    result = callback({"error": "access_denied", "state": "state-abc"})

    assert result["statusCode"] == 200
    assert "not connected" in result["body"]
    assert exchange["exchange"] == []
    assert pending_auth_store().get("n1") is None  # the nonce is burned either way


def test_failed_exchange_returns_an_error_page(pending, monkeypatch):
    def boom(cimd, code):
        raise RuntimeError("invalid_grant")

    monkeypatch.setattr(oauth_callback.cimd_client, "exchange_code", boom)

    assert callback({"code": "code-1", "state": "state-abc"})["statusCode"] == 502


def test_cimd_records_do_not_take_the_agentcore_path(pending, monkeypatch):
    """A CIMD record must never reach CompleteResourceTokenAuth."""

    class Unexpected:
        def complete_resource_token_auth(self, **kwargs):
            raise AssertionError("AgentCore path taken for a CIMD consent")

    monkeypatch.setattr(oauth_callback, "_identity", lambda: Unexpected())

    assert callback({"session_id": "urn:session:abc"})["statusCode"] == 403
