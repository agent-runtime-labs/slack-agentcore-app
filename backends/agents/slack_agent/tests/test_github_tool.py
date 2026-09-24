import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import github  # noqa: E402
from strands.types.exceptions import MaxTokensReachedException, MCPClientInitializationError  # noqa: E402


@pytest.fixture
def calls(monkeypatch):
    log = {"fetch": [], "mcp": []}
    responses = {"fetch": [], "mcp": []}

    def fake_fetch(workload_token, force=False):
        log["fetch"].append((workload_token, force))
        return responses["fetch"].pop(0)

    def fake_run_github_mcp_agent(access_token, request):
        log["mcp"].append((access_token, request))
        result = responses["mcp"].pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(github, "fetch_token", fake_fetch)
    monkeypatch.setattr(github, "_run_github_mcp_agent", fake_run_github_mcp_agent)
    return log, responses


def run_tool(state=None, token="wat-alice", request="show my profile"):
    state = state or github.AuthState()
    tool = github.build_github_tool(lambda: token, state)
    return tool(request=request), state


def test_returns_mcp_result_when_token_exists(calls):
    log, responses = calls
    responses["fetch"].append({"accessToken": "gh-token"})
    responses["mcp"].append('{"login": "alice"}')

    output, state = run_tool()

    assert output == '{"login": "alice"}'
    assert state.as_dict() is None
    assert log["fetch"] == [("wat-alice", False)]
    assert log["mcp"] == [("gh-token", "show my profile")]


def test_requests_consent_when_no_token(calls):
    _, responses = calls
    responses["fetch"].append({"authorizationUrl": "https://gh/auth", "sessionUri": "urn:s1"})

    output, state = run_tool()

    assert output.startswith("AUTHORIZATION_REQUIRED")
    # `cimd` stays None: this is an AgentCore Identity consent, not a CIMD one.
    assert state.as_dict() == {
        "provider": "GitHub",
        "authorizationUrl": "https://gh/auth",
        "sessionUri": "urn:s1",
        "cimd": None,
    }


def test_revoked_token_forces_reauthentication(calls):
    log, responses = calls
    responses["fetch"] += [{"accessToken": "stale"}, {"authorizationUrl": "https://gh/auth", "sessionUri": "urn:s2"}]
    responses["mcp"].append(MCPClientInitializationError("the client initialization failed: 401 Unauthorized"))

    output, state = run_tool()

    assert output.startswith("AUTHORIZATION_REQUIRED")
    assert log["fetch"] == [("wat-alice", False), ("wat-alice", True)]
    assert state.session_uri == "urn:s2"


def test_errors_are_reported_to_model(calls):
    _, responses = calls
    responses["fetch"].append({"accessToken": "gh-token"})
    responses["mcp"].append(RuntimeError("boom"))

    assert run_tool()[0] == "ERROR: could not reach GitHub right now"


def test_max_tokens_reached_is_reported_without_swallowing_success(calls):
    _, responses = calls
    responses["fetch"].append({"accessToken": "gh-token"})
    responses["mcp"].append(MaxTokensReachedException("Model stopped generating due to maximum token limit."))

    output = run_tool()[0]

    assert output.startswith("ERROR:")
    assert "too much output" in output


def test_check_connection_reports_connected(calls):
    _, responses = calls
    responses["fetch"].append({"accessToken": "gh-token"})

    result = github.check_connection(lambda: "wat-alice")

    assert result == {"connected": True, "authorizationUrl": None, "sessionUri": None}


def test_check_connection_reports_not_connected(calls):
    _, responses = calls
    responses["fetch"].append({"authorizationUrl": "https://gh/auth", "sessionUri": "urn:s1"})

    result = github.check_connection(lambda: "wat-alice")

    assert result == {"connected": False, "authorizationUrl": "https://gh/auth", "sessionUri": "urn:s1"}


def test_check_connection_failure_reports_not_connected():
    def boom():
        raise RuntimeError("no workload token")

    result = github.check_connection(boom)

    assert result == {"connected": False, "authorizationUrl": None, "sessionUri": None}
