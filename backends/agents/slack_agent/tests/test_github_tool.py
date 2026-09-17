import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import github  # noqa: E402


@pytest.fixture
def calls(monkeypatch):
    log = {"fetch": [], "user": []}
    responses = {"fetch": [], "user": []}

    def fake_fetch(workload_token, force=False):
        log["fetch"].append((workload_token, force))
        return responses["fetch"].pop(0)

    def fake_user(access_token):
        log["user"].append(access_token)
        return responses["user"].pop(0)

    monkeypatch.setattr(github, "fetch_token", fake_fetch)
    monkeypatch.setattr(github, "call_user", fake_user)
    return log, responses


def run_tool(state=None, token="wat-alice"):
    state = state or github.AuthState()
    tool = github.build_github_tool(lambda: token, state)
    return tool(), state


def test_returns_profile_when_token_exists(calls):
    log, responses = calls
    responses["fetch"].append({"accessToken": "gh-token"})
    responses["user"].append((200, {"login": "alice"}))

    output, state = run_tool()

    assert json.loads(output) == {"login": "alice"}
    assert state.as_dict() is None
    assert log["fetch"] == [("wat-alice", False)]


def test_requests_consent_when_no_token(calls):
    _, responses = calls
    responses["fetch"].append({"authorizationUrl": "https://gh/auth", "sessionUri": "urn:s1"})

    output, state = run_tool()

    assert output.startswith("AUTHORIZATION_REQUIRED")
    assert state.as_dict() == {"provider": "GitHub", "authorizationUrl": "https://gh/auth", "sessionUri": "urn:s1"}


def test_revoked_token_forces_reauthentication(calls):
    log, responses = calls
    responses["fetch"] += [{"accessToken": "stale"}, {"authorizationUrl": "https://gh/auth", "sessionUri": "urn:s2"}]
    responses["user"].append((401, {"error": "Unauthorized"}))

    output, state = run_tool()

    assert output.startswith("AUTHORIZATION_REQUIRED")
    assert log["fetch"] == [("wat-alice", False), ("wat-alice", True)]
    assert state.session_uri == "urn:s2"


def test_errors_are_reported_to_model(calls):
    _, responses = calls
    responses["fetch"].append({"accessToken": "gh-token"})
    responses["user"].append((500, {}))
    assert run_tool()[0] == "ERROR: GitHub returned HTTP 500"
