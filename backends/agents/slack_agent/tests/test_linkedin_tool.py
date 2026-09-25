import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import linkedin  # noqa: E402


@pytest.fixture
def calls(monkeypatch):
    log = {"fetch": [], "userinfo": []}
    responses = {"fetch": [], "userinfo": []}

    def fake_fetch(workload_token, force=False):
        log["fetch"].append((workload_token, force))
        return responses["fetch"].pop(0)

    def fake_userinfo(access_token):
        log["userinfo"].append(access_token)
        return responses["userinfo"].pop(0)

    monkeypatch.setattr(linkedin, "fetch_token", fake_fetch)
    monkeypatch.setattr(linkedin, "call_userinfo", fake_userinfo)
    return log, responses


def run_tool(state=None, token="wat-alice"):
    state = state or linkedin.AuthState()
    tool = linkedin.build_linkedin_tool(lambda: token, state)
    return tool(), state


def test_returns_profile_when_token_exists(calls):
    log, responses = calls
    responses["fetch"].append({"accessToken": "li-token"})
    responses["userinfo"].append((200, {"name": "Alice"}))

    output, state = run_tool()

    assert json.loads(output) == {"name": "Alice"}
    assert state.as_dict() is None
    assert log["fetch"] == [("wat-alice", False)]


def test_requests_consent_when_no_token(calls):
    _, responses = calls
    responses["fetch"].append({"authorizationUrl": "https://li/auth", "sessionUri": "urn:s1"})

    output, state = run_tool()

    assert output.startswith("AUTHORIZATION_REQUIRED")
    # `cimd` stays None: this is an AgentCore Identity consent, not a CIMD one.
    assert state.as_dict() == {
        "provider": "LinkedIn",
        "authorizationUrl": "https://li/auth",
        "sessionUri": "urn:s1",
        "cimd": None,
    }


def test_revoked_token_forces_reauthentication(calls):
    log, responses = calls
    responses["fetch"] += [{"accessToken": "stale"}, {"authorizationUrl": "https://li/auth", "sessionUri": "urn:s2"}]
    responses["userinfo"].append((401, {"error": "Unauthorized"}))

    output, state = run_tool()

    assert output.startswith("AUTHORIZATION_REQUIRED")
    assert log["fetch"] == [("wat-alice", False), ("wat-alice", True)]
    assert state.session_uri == "urn:s2"


def test_errors_are_reported_to_model(calls):
    _, responses = calls
    responses["fetch"].append({"accessToken": "li-token"})
    responses["userinfo"].append((500, {}))
    assert run_tool()[0] == "ERROR: LinkedIn returned HTTP 500"


def test_workload_token_prefers_runtime_context(monkeypatch):
    monkeypatch.setattr(linkedin.config, "LOCAL_WORKLOAD_NAME", "")
    assert linkedin.workload_token_provider("from-runtime", "u1")() == "from-runtime"
    with pytest.raises(RuntimeError):
        linkedin.workload_token_provider(None, "u1")()


def test_workload_token_local_fallback(monkeypatch):
    class FakeIdentity:
        def get_workload_access_token_for_user_id(self, workloadName, userId):
            return {"workloadAccessToken": f"{workloadName}:{userId}"}

    monkeypatch.setattr(linkedin.config, "LOCAL_WORKLOAD_NAME", "local-wl")
    monkeypatch.setattr(linkedin, "_identity", lambda: FakeIdentity())
    assert linkedin.workload_token_provider(None, "slack-T1-U1")() == "local-wl:slack-T1-U1"
