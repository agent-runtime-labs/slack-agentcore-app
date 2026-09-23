"""The CIMD tool's token lifecycle: use, refresh, reconnect, consent.

Nothing here touches the network, DynamoDB or Bedrock: discovery, the MCP sub-agent and
the token store are all replaced with fakes, so what is under test is the decision
logic -- which of the four paths a request takes, and what the user is asked to do.
"""

import base64
import hashlib
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import config  # noqa: E402
from auth_state import AuthState  # noqa: E402
from cimd import oauth, tool as cimd_tool  # noqa: E402
from cimd.discovery import AuthorizationServer  # noqa: E402
from cimd.providers import LINEAR  # noqa: E402
from cimd.tokens import StoredToken  # noqa: E402
from strands.types.exceptions import MaxTokensReachedException, MCPClientInitializationError  # noqa: E402

SERVER = AuthorizationServer(
    issuer="https://mcp.linear.app",
    authorization_endpoint="https://mcp.linear.app/authorize",
    token_endpoint="https://mcp.linear.app/token",
    resource="https://mcp.linear.app/mcp",
    scopes_supported=("read", "write"),
    returns_iss=True,
)
USER = "slack-T999-UALICE"


class FakeStore:
    def __init__(self, token: StoredToken | None = None):
        self.items = {(token.user_id, token.provider): token} if token else {}
        self.deleted: list[tuple[str, str]] = []

    def get(self, user_id, provider):
        return self.items.get((user_id, provider))

    def put(self, token):
        self.items[(token.user_id, token.provider)] = token

    def delete(self, user_id, provider):
        self.deleted.append((user_id, provider))
        self.items.pop((user_id, provider), None)


def stored(expires_in=3600, refresh_token="rt-1") -> StoredToken:
    return StoredToken(
        user_id=USER,
        provider="linear",
        access_token="at-1",
        refresh_token=refresh_token,
        access_token_expires_at=int(time.time()) + expires_in,
        scope="read write",
        issuer=SERVER.issuer,
    )


@pytest.fixture(autouse=True)
def cimd_env(monkeypatch):
    monkeypatch.setattr(config, "CIMD_CLIENT_ID", "https://api.example.com/oauth2/client-metadata.json")
    monkeypatch.setattr(config, "OAUTH2_RETURN_URL", "https://api.example.com/oauth2/callback")
    monkeypatch.setattr(config, "CIMD_TOKEN_TABLE", "cimd-tokens")
    monkeypatch.setattr(config, "CIMD_PROVIDERS", ["linear"])
    monkeypatch.setattr(cimd_tool, "discover", lambda provider: SERVER)


@pytest.fixture
def mcp_calls(monkeypatch):
    """Replaces the nested MCP agent; queue results (or exceptions) to return."""
    log, results = [], []

    def fake_run(provider, access_token, request):
        log.append((provider.key, access_token, request))
        result = results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(cimd_tool, "run_mcp_agent", fake_run)
    return log, results


def run_tool(store, request="list my open issues"):
    state = AuthState()
    tool = cimd_tool.build_cimd_tool(LINEAR, USER, state, store)
    return tool(request=request), state


def test_uses_a_valid_stored_token(mcp_calls):
    log, results = mcp_calls
    results.append("ENG-1 Fix the thing")

    output, state = run_tool(FakeStore(stored()))

    assert output == "ENG-1 Fix the thing"
    assert state.as_dict() is None
    assert log == [("linear", "at-1", "list my open issues")]


def test_asks_for_consent_when_not_connected(mcp_calls):
    output, state = run_tool(FakeStore())

    assert output.startswith("AUTHORIZATION_REQUIRED")
    payload = state.as_dict()
    assert payload["provider"] == "Linear"
    assert payload["sessionUri"] is None  # not an AgentCore Identity consent
    assert payload["cimd"]["provider"] == "linear"
    assert payload["cimd"]["tokenEndpoint"] == SERVER.token_endpoint
    assert payload["cimd"]["clientId"] == config.CIMD_CLIENT_ID


def test_authorization_url_carries_pkce_and_the_client_url(mcp_calls):
    _, state = run_tool(FakeStore())

    url = urlsplit(state.authorization_url)
    params = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert f"{url.scheme}://{url.netloc}{url.path}" == SERVER.authorization_endpoint
    # The client_id *is* the metadata document URL -- that is the whole registration.
    assert params["client_id"] == config.CIMD_CLIENT_ID
    assert params["redirect_uri"] == config.OAUTH2_RETURN_URL
    assert params["scope"] == "read write"
    assert params["resource"] == SERVER.resource
    assert params["code_challenge_method"] == "S256"
    assert params["state"] == state.cimd["state"]

    # Only the hash travels through the browser; the verifier stays in our infrastructure.
    verifier = state.cimd["codeVerifier"]
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert params["code_challenge"] == expected
    assert verifier not in state.authorization_url


def test_refreshes_an_expired_token(monkeypatch, mcp_calls):
    log, results = mcp_calls
    results.append("done")
    store = FakeStore(stored(expires_in=-10))
    refreshed = stored(expires_in=3600)
    monkeypatch.setattr(
        oauth, "refresh", lambda provider, server, token: StoredToken(**{**vars(refreshed), "access_token": "at-2"})
    )

    output, state = run_tool(store)

    assert output == "done"
    assert state.as_dict() is None
    assert log[0][1] == "at-2"
    assert store.get(USER, "linear").access_token == "at-2"


def test_failed_refresh_forgets_the_connection(monkeypatch, mcp_calls):
    store = FakeStore(stored(expires_in=-10))

    def boom(provider, server, token):
        raise RuntimeError("invalid_grant")

    monkeypatch.setattr(oauth, "refresh", boom)

    output, state = run_tool(store)

    assert output.startswith("AUTHORIZATION_REQUIRED")
    assert store.deleted == [(USER, "linear")]
    assert state.cimd is not None


def test_expired_token_without_refresh_token_asks_again(mcp_calls):
    output, _ = run_tool(FakeStore(stored(expires_in=-10, refresh_token="")))

    assert output.startswith("AUTHORIZATION_REQUIRED")


def test_401_from_the_mcp_server_forces_a_reconnect(mcp_calls):
    _, results = mcp_calls
    results.append(MCPClientInitializationError("server returned 401 Unauthorized"))
    store = FakeStore(stored())

    output, state = run_tool(store)

    assert output.startswith("AUTHORIZATION_REQUIRED")
    assert store.deleted == [(USER, "linear")]
    assert state.cimd["provider"] == "linear"


def test_other_mcp_errors_do_not_drop_the_connection(mcp_calls):
    _, results = mcp_calls
    results.append(MCPClientInitializationError("server returned 503"))
    store = FakeStore(stored())

    output, state = run_tool(store)

    assert output.startswith("ERROR")
    assert store.deleted == []
    assert state.as_dict() is None


def test_oversized_answer_is_reported_not_retried(mcp_calls):
    _, results = mcp_calls
    results.append(MaxTokensReachedException("too long"))

    output, state = run_tool(FakeStore(stored()))

    assert "narrower" in output
    assert state.as_dict() is None


def test_tools_are_named_after_the_provider(mcp_calls):
    tool = cimd_tool.build_cimd_tool(LINEAR, USER, AuthState(), FakeStore())
    assert tool.tool_name == "use_linear"
    assert "Linear" in tool.tool_spec["description"]


def test_no_token_table_means_no_cimd_tools(monkeypatch):
    monkeypatch.setattr(config, "CIMD_TOKEN_TABLE", "")
    assert cimd_tool.build_cimd_tools(USER, AuthState()) == []
