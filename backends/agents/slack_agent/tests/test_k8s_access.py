"""Read boundaries, per-request credentials, and failure modes."""

import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from auth_state import AuthState
import k8s_access
from k8s_access import Access, bounded_read, user_client
from k8s_auth.settings import AuthFailure, Busy, Settings
from kubernetes.client.exceptions import ApiException


@pytest.fixture
def settings():
    return Settings("lab", "https://issuer.example/pinniped", "aud-lab", "https://proxy.example:4433", "",
                    "jwt", "client.oauth.pinniped.dev-mitra", "secret", "table", "https://app.example/oauth2/callback")


@pytest.fixture
def access(settings):
    store, protocol = Mock(), Mock()
    store.get.return_value = dict(version="v1", refresh_token="refresh-a", subject="alice", expires_at=100)
    store.save_refresh.return_value = dict(subject="alice", version="v2")
    protocol.consent.return_value = ("https://issuer.example/login", {"nonce": "nonce"})
    protocol.credential.return_value = {"token": "short-lived"}
    return Access(settings, "slack-T1-UA", AuthState(), store, protocol)


@pytest.mark.parametrize("operation,namespace,pod,tail", [("exec", "ns", "", 100), ("pods", "../ns", "", 100),
    ("logs", "ns", "pod", 201), ("logs", "ns", "../pod", 1), ("logs", "ns", "pod", True)])
def test_invalid_operations_never_touch_tokens(access, operation, namespace, pod, tail):
    access.read(operation, namespace, pod, tail_lines=tail)
    access.store.get.assert_not_called()
    access.protocol.refresh.assert_not_called()


def test_no_connection_requires_private_auth(access):
    access.store.get.return_value = None
    assert access.read("pods", "ns").startswith("AUTHORIZATION_REQUIRED")
    assert access.auth_state.kubernetes == {"nonce": "nonce"}


def test_failed_refresh_requires_auth_and_invalidates_only_owner(access):
    row = access.store.get.return_value
    access.protocol.refresh.side_effect = AuthFailure()
    assert access.read("pods", "ns").startswith("AUTHORIZATION_REQUIRED")
    access.store.invalidate.assert_called_once_with(access.user_id, row, access.store.lock.return_value)
    access.protocol.credential.assert_not_called()


def test_abandoned_refresh_never_reuses_rotating_token(access):
    row = access.store.get.return_value
    row.update(lock_until=int(time.time()) - 1, lock_owner="dead-worker")
    assert access.read("pods", "ns").startswith("AUTHORIZATION_REQUIRED")
    access.protocol.refresh.assert_not_called()
    access.store.invalidate.assert_called_once_with(access.user_id, row, "dead-worker")


def test_busy_refresh_has_no_fallback(access):
    access.store.lock.side_effect = Busy()
    assert "retry" in access.read("pods", "ns")
    access.protocol.refresh.assert_not_called()


@pytest.mark.parametrize("status,expected", [(403, "ACCESS_DENIED"), (401, "AUTHORIZATION_REQUIRED"), (500, "unavailable")])
def test_api_authorization_status_and_safe_errors(access, monkeypatch, status, expected):
    @contextmanager
    def api(*args):
        yield object()
    monkeypatch.setattr(k8s_access, "user_client", api)
    monkeypatch.setattr(k8s_access, "bounded_read", Mock(side_effect=ApiException(status=status, reason="secret-remote-body")))
    result = access.read("pods", "ns")
    assert expected in result and "secret-remote-body" not in result
    if status == 403:
        access.store.invalidate.assert_not_called()
        access.protocol.consent.assert_not_called()


def test_tls_and_tunnel_failure_is_sanitized(access, monkeypatch):
    monkeypatch.setattr(k8s_access, "user_client", Mock(side_effect=ValueError("private-key")))
    result = access.read("pods", "ns")
    assert "connection or authentication failed" in result
    assert "private-key" not in result


def test_mtls_files_private_and_cleaned(settings, monkeypatch):
    captured = []
    class Api:
        def __init__(self, config):
            captured.append(config)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
    monkeypatch.setattr(k8s_access.client, "ApiClient", Api)
    first = {"clientCertificateData": "cert-a", "clientKeyData": "key-a"}
    with user_client(settings, first):
        config = captured[0]
        paths = [config.cert_file, config.key_file]
        assert all(os.stat(p).st_mode & 0o777 == 0o600 for p in paths)
        assert config.verify_ssl is True and config.retries == 0
        assert Path(config.key_file).read_text() == "key-a"
        with user_client(settings, {"clientCertificateData": "cert-b", "clientKeyData": "key-b"}):
            assert captured[1].key_file != config.key_file
            assert Path(captured[1].key_file).read_text() == "key-b"
    assert all(not Path(p).exists() for p in paths)


def test_log_read_enforces_both_server_and_client_bound(monkeypatch):
    response = Mock()
    response.read.return_value = b"synthetic logs"
    core = Mock()
    core.read_namespaced_pod_log.return_value = response
    monkeypatch.setattr(k8s_access.client, "CoreV1Api", Mock(return_value=core))
    assert bounded_read(object(), "logs", "ns", "pod", tail_lines=25)["logs"] == "synthetic logs"
    options = core.read_namespaced_pod_log.call_args.kwargs
    assert options["follow"] is False and options["limit_bytes"] == 16000 and options["tail_lines"] == 25
    response.read.assert_called_once_with(16000, decode_content=True)
    response.close.assert_called_once()
    response.release_conn.assert_called_once()
