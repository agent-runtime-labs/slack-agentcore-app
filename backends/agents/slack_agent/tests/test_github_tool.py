import http.server
import sys
import threading
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


@pytest.fixture
def mcp_stub(monkeypatch):
    """A loopback HTTP server standing in for GitHub's MCP endpoint, so the real MCPClient stack
    is exercised end to end. It answers every request with the status set in `stub["status"]`."""
    stub = {"status": 401, "authorization": []}

    class Handler(http.server.BaseHTTPRequestHandler):
        def _respond(self):
            self.rfile.read(int(self.headers.get("content-length") or 0))
            stub["authorization"].append(self.headers.get("Authorization"))
            self.send_response(stub["status"])
            self.send_header("content-length", "0")
            self.end_headers()

        do_GET = do_POST = do_DELETE = _respond

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    for var in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(var, "127.0.0.1")
    monkeypatch.setattr(github, "MCP_SERVER_URL", f"http://127.0.0.1:{server.server_address[1]}/mcp/")
    yield stub
    server.shutdown()
    server.server_close()


@pytest.fixture
def fetches(monkeypatch):
    log = []
    responses = []

    def fake_fetch(workload_token, force=False):
        log.append((workload_token, force))
        return responses.pop(0)

    monkeypatch.setattr(github, "fetch_token", fake_fetch)
    return log, responses


def test_mcp_server_401_forces_reauthentication(mcp_stub, fetches):
    # The MCP client reports every HTTP error with the same generic text, so the 401 has to be
    # detected from the response status itself, not from the exception message.
    log, responses = fetches
    responses += [{"accessToken": "revoked"}, {"authorizationUrl": "https://gh/auth", "sessionUri": "urn:s3"}]

    output, state = run_tool()

    assert output.startswith("AUTHORIZATION_REQUIRED")
    assert log == [("wat-alice", False), ("wat-alice", True)]
    assert state.session_uri == "urn:s3"
    assert mcp_stub["authorization"] and set(mcp_stub["authorization"]) == {"Bearer revoked"}


def test_mcp_server_500_is_an_error_not_a_consent_prompt(mcp_stub, fetches):
    log, responses = fetches
    mcp_stub["status"] = 500
    responses.append({"accessToken": "gh-token"})

    output, state = run_tool()

    assert output == "ERROR: could not reach GitHub right now"
    assert log == [("wat-alice", False)]
    assert state.as_dict() is None
    assert set(mcp_stub["authorization"]) == {"Bearer gh-token"}
