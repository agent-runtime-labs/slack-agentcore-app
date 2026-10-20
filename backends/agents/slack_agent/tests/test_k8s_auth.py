"""Real JWT validation and DynamoDB transaction semantics, with no external services."""

import base64
import hashlib
import json
import sys
import time
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import boto3
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from auth_state import AuthState
from k8s_auth.protocol import Protocol, SCOPES
from k8s_auth.settings import AuthFailure, Busy, Settings, https
from k8s_auth.store import Store


@pytest.fixture
def settings():
    return Settings("lab", "https://issuer.example/pinniped", "aud-lab", "https://proxy.example:4433", "",
                    "lab-jwt", "client.oauth.pinniped.dev-mitra", "secret-arn", "tokens", "https://app.example/oauth2/callback")


@pytest.fixture
def protocol(settings, monkeypatch):
    protocol = Protocol(settings)
    private = ec.generate_private_key(ec.SECP256R1())
    jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(private.public_key()))
    jwk.update(kid="one", use="sig", alg="ES256")
    metadata = dict(issuer=settings.issuer, authorization_endpoint=settings.issuer + "/authorize",
                    token_endpoint=settings.issuer + "/token", jwks_uri=settings.issuer + "/jwks")
    monkeypatch.setattr(protocol, "_request", lambda method, url, **kwargs:
                        {"keys": [jwk]} if url.endswith("jwks") else metadata)
    def sign(**overrides):
        return jwt.encode({**dict(iss=settings.issuer, sub="alice-sub", aud=settings.client_id,
            exp=int(time.time()) + 300, iat=int(time.time())), **overrides}, private,
            algorithm="ES256", headers={"kid": "one"})
    return protocol, sign


def test_pkce_state_nonce(protocol):
    protocol, _ = protocol
    url, payload = protocol.consent()
    query = parse_qs(urlsplit(url).query)
    expected = base64.urlsafe_b64encode(hashlib.sha256(payload["code_verifier"].encode()).digest()).rstrip(b"=").decode()
    assert query["code_challenge"] == [expected]
    assert query["nonce"] == [payload["nonce"]]
    assert query["state"] == [payload["state"]]
    assert query["scope"] == [SCOPES]
    assert payload["code_verifier"] not in url


@pytest.mark.parametrize("field,value", [("iss", "https://evil.example"), ("aud", "wrong"), ("nonce", "wrong"),
                                            ("sub", "bob-sub"), ("exp", 1), ("azp", "wrong")])
def test_signed_id_claim_mismatches_rejected(protocol, field, value):
    protocol, sign = protocol
    base = dict(nonce="nonce")
    base[field] = value
    with pytest.raises(AuthFailure):
        protocol.verify_id(sign(**base), protocol.settings.client_id, nonce="nonce", subject="alice-sub")


def test_valid_jwt_and_required_nonce(protocol):
    protocol, sign = protocol
    assert protocol.verify_id(sign(nonce="nonce"), protocol.settings.client_id, nonce="nonce")["sub"] == "alice-sub"
    with pytest.raises(AuthFailure):
        protocol.verify_id(sign(), protocol.settings.client_id, nonce="nonce")


def test_discovery_pinned_to_issuer(protocol, monkeypatch):
    protocol, _ = protocol
    monkeypatch.setattr(protocol, "_request", lambda *args, **kwargs: dict(issuer=protocol.settings.issuer,
        authorization_endpoint="https://evil.example/auth", token_endpoint=protocol.settings.issuer + "/token",
        jwks_uri=protocol.settings.issuer + "/jwks"))
    with pytest.raises(AuthFailure):
        protocol.discover()


def test_exchange_uses_rfc8693_access_token_response(protocol, monkeypatch):
    protocol, sign = protocol
    seen = []
    cluster_token = sign(aud=protocol.settings.audience)
    monkeypatch.setattr(protocol, "_token", lambda fields: seen.append(fields) or dict(
        access_token=cluster_token, issued_token_type="urn:ietf:params:oauth:token-type:jwt", token_type="N_A"))
    original = protocol._request
    monkeypatch.setattr(protocol, "_request", lambda method, url, **kwargs:
        {"status": {"credential": {"clientCertificateData": "cert", "clientKeyData": "private-key", "expirationTimestamp": "2099-01-01T00:00:00Z"}}}
        if method == "POST" else original(method, url, **kwargs))
    assert protocol.credential(dict(access_token="opaque", subject="alice-sub"))["clientCertificateData"] == "cert"
    assert seen[0]["subject_token"] == "opaque"
    assert seen[0]["audience"] == "aud-lab"


def test_flow_discriminator_prevents_cross_provider_state():
    state = AuthState()
    state.request("Kubernetes", "https://issuer/login", kubernetes={"nonce": "one"})
    state.request("GitHub", "https://github/login", session_uri="session")
    assert "kubernetes" not in state.as_dict()
    state.request("Linear", "https://linear/login", cimd={"state": "linear"})
    state.request("Kubernetes", "https://issuer/login", kubernetes={"nonce": "two"})
    assert state.as_dict()["cimd"] is None and state.as_dict()["sessionUri"] is None


@pytest.fixture
def store(settings, monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        boto3.client("dynamodb").create_table(TableName=settings.table, BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "user_id", "KeyType": "HASH"}, {"AttributeName": "cluster", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": n, "AttributeType": "S"} for n in ("user_id", "cluster")])
        yield Store(settings)


def session():
    return dict(access_token="opaque", refresh_token="refresh-1", expires_at=int(time.time()) + 300,
                username="alice", subject="alice-sub")


def test_activation_requires_same_workspace_user_and_is_single_use(store):
    code = store.stage("slack-T1-UA", session(), "{}")
    assert store.get("slack-T1-UA") is None
    assert store.activate("slack-T2-UA", code) is None
    assert store.activate("slack-T1-UB", code) is None
    assert store.activate("slack-T1-UA", code)
    assert store.activate("slack-T1-UA", code) is None
    assert store.get("slack-T1-UA")["subject"] == "alice-sub"


def test_refresh_serialization_and_relink_fencing(store):
    user = "slack-T1-UA"
    store.activate(user, store.stage(user, session(), "{}"))
    row = store.get(user)
    owner = store.lock(user, row)
    with pytest.raises(Busy):
        store.lock(user, row)
    # New confirmation replaces old active connection; in-flight old refresh cannot overwrite it.
    store.activate(user, store.stage(user, {**session(), "subject": "new-sub"}, "{}"))
    with pytest.raises(AuthFailure):
        store.save_refresh(user, row, owner, session())
    store.invalidate(user, row, owner)
    assert store.get(user)["subject"] == "new-sub"


def test_refresh_persists_rotated_token_then_can_lock_new_version(store):
    user = "slack-T1-UA"
    store.activate(user, store.stage(user, session(), "{}"))
    row = store.get(user)
    refreshed = store.save_refresh(user, row, store.lock(user, row), {**session(), "refresh_token": "refresh-2"})
    assert store.get(user)["refresh_token"] == "refresh-2"
    assert store.lock(user, refreshed)


def test_expired_staging_and_changed_cluster_contract_rejected(store):
    user = "slack-T1-UA"
    code = store.stage(user, session(), "{}")
    store.table.update_item(Key={"user_id": user, "cluster": "lab#pending#" + code},
        UpdateExpression="SET #ttl=:expired", ExpressionAttributeNames={"#ttl": "ttl"}, ExpressionAttributeValues={":expired": 1})
    assert store.activate(user, code) is None
    store.activate(user, store.stage(user, session(), "{}"))
    store.settings = replace(store.settings, audience="different-audience")
    assert store.get(user) is None


def test_mirrored_protocol_sources_stay_identical():
    root = Path(__file__).resolve().parents[4]
    source = root / "backends/agents/slack_agent/src/k8s_auth"
    target = root / "backends/lambdas/src/slack_app/k8s_auth"
    for path in source.glob("*.py"):
        assert path.read_bytes() == (target / path.name).read_bytes()


@pytest.mark.parametrize("endpoint", ["http://host", "https://user:password@host", "https://host?x=1", "file:///tmp/a"])
def test_endpoints_require_tls_and_disallow_embedded_secrets(endpoint):
    with pytest.raises(ValueError):
        https(endpoint)


def test_bad_signature_unknown_key_and_unsigned_tokens_rejected(protocol):
    protocol, sign = protocol
    token = sign()
    other = ec.generate_private_key(ec.SECP256R1())
    claims = jwt.decode(token, options={"verify_signature": False})
    invalid_tokens = [jwt.encode(claims, other, algorithm="ES256", headers={"kid": "one"}),
        jwt.encode(claims, other, algorithm="ES256", headers={"kid": "unknown"}),
        jwt.encode(claims, key="", algorithm="none")]
    for invalid in invalid_tokens:
        with pytest.raises(AuthFailure):
            protocol.verify_id(invalid, protocol.settings.client_id)


def test_access_token_hash_mismatch_rejected(protocol):
    protocol, sign = protocol
    with pytest.raises(AuthFailure):
        protocol.verify_id(sign(at_hash="wrong"), protocol.settings.client_id, access_token="opaque")


def test_cluster_token_keeps_client_as_authorized_party(protocol):
    protocol, sign = protocol
    claims = protocol.verify_id(sign(aud=protocol.settings.audience, azp=protocol.settings.client_id),
        protocol.settings.audience, subject="alice-sub")
    assert claims["aud"] == protocol.settings.audience
    with pytest.raises(AuthFailure):
        protocol.verify_id(sign(aud=protocol.settings.audience, azp=protocol.settings.audience),
            protocol.settings.audience, subject="alice-sub")


@pytest.mark.parametrize("key_count", [1, 2])
def test_pinniped_token_without_kid_requires_single_signing_key(settings, monkeypatch, key_count):
    private = ec.generate_private_key(ec.SECP256R1())
    keys = []
    for index in range(key_count):
        public = private.public_key() if index == 0 else ec.generate_private_key(ec.SECP256R1()).public_key()
        key = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(public))
        keys.append({**key, "kid": str(index), "use": "sig", "alg": "ES256"})
    protocol = Protocol(settings)
    monkeypatch.setattr(protocol, "discover", lambda: {"jwks_uri": settings.issuer + "/jwks"})
    monkeypatch.setattr(protocol, "_request", lambda *args, **kwargs: {"keys": keys})
    token = jwt.encode(dict(iss=settings.issuer, sub="alice-sub", aud=settings.client_id,
        exp=int(time.time()) + 300, iat=int(time.time())), private, algorithm="ES256")
    if key_count == 1:
        assert protocol.verify_id(token, settings.client_id)["sub"] == "alice-sub"
    else:
        with pytest.raises(AuthFailure):
            protocol.verify_id(token, settings.client_id)


def test_request_tls_failure_omits_remote_exception_text(settings, monkeypatch):
    import k8s_auth.protocol as module
    monkeypatch.setattr(module.httpx, "Client", lambda **kwargs: (_ for _ in ()).throw(ValueError("secret-body")))
    with pytest.raises(AuthFailure) as error:
        Protocol(settings)._request("GET", "https://issuer.example")
    assert "secret-body" not in str(error.value)


@pytest.mark.parametrize("kid", [None, "", 0, []])
def test_explicit_malformed_kid_is_rejected(settings, monkeypatch, kid):
    private = ec.generate_private_key(ec.SECP256R1())
    key = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(private.public_key()))
    key.update(use="sig", alg="ES256", kid="one")
    protocol = Protocol(settings)
    monkeypatch.setattr(protocol, "discover", lambda: {"jwks_uri": settings.issuer + "/jwks"})
    monkeypatch.setattr(protocol, "_request", lambda *args, **kwargs: {"keys": [key]})
    header = base64.urlsafe_b64encode(json.dumps({"alg": "ES256", "kid": kid}).encode()).rstrip(b"=").decode()
    token = header + ".e30.signature"
    with pytest.raises(AuthFailure):
        protocol.verify_id(token, settings.client_id)
