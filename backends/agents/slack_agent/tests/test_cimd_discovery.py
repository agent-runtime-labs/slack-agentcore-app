"""Discovery: walking from an MCP endpoint to its authorization server.

The well-known lookups are the fiddly part -- RFC 9728 and RFC 8414 both insert the
well-known segment *between* the host and the path, which is easy to get wrong and
silently returns 404 when you do.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cimd import discovery  # noqa: E402
from cimd.providers import LINEAR, NOTION, CimdProvider  # noqa: E402

METADATA = {
    "issuer": "https://mcp.linear.app",
    "authorization_endpoint": "https://mcp.linear.app/authorize",
    "token_endpoint": "https://mcp.linear.app/token",
    "code_challenge_methods_supported": ["S256"],
    "client_id_metadata_document_supported": True,
    "authorization_response_iss_parameter_supported": True,
    "scopes_supported": ["read", "write"],
}


def test_well_known_urls_put_the_path_after_the_suffix():
    assert discovery._well_known_urls("https://mcp.linear.app/mcp", discovery.PROTECTED_RESOURCE) == [
        "https://mcp.linear.app/.well-known/oauth-protected-resource/mcp",
        "https://mcp.linear.app/.well-known/oauth-protected-resource",
    ]


def test_well_known_urls_for_a_path_less_issuer():
    assert discovery._well_known_urls("https://mcp.linear.app", discovery.AUTHORIZATION_SERVER) == [
        "https://mcp.linear.app/.well-known/oauth-authorization-server",
    ]


def test_builds_an_authorization_server_from_metadata():
    server = discovery._build(METADATA, "https://mcp.linear.app", "https://mcp.linear.app/mcp")

    assert server.token_endpoint == "https://mcp.linear.app/token"
    assert server.resource == "https://mcp.linear.app/mcp"
    assert server.returns_iss is True


def test_rejects_a_server_that_says_it_does_not_do_cimd():
    metadata = {**METADATA, "client_id_metadata_document_supported": False}

    with pytest.raises(discovery.CimdUnsupported):
        discovery._build(metadata, "https://example.com", "")


def test_allows_a_server_that_simply_does_not_advertise_cimd():
    metadata = {k: v for k, v in METADATA.items() if k != "client_id_metadata_document_supported"}

    assert discovery._build(metadata, "https://mcp.linear.app", "").issuer == "https://mcp.linear.app"


def test_requires_pkce_s256():
    metadata = {**METADATA, "code_challenge_methods_supported": ["plain"]}

    with pytest.raises(RuntimeError, match="PKCE"):
        discovery._build(metadata, "https://example.com", "")


def test_rejects_plaintext_endpoints():
    metadata = {**METADATA, "token_endpoint": "http://mcp.linear.app/token"}

    with pytest.raises(ValueError, match="https"):
        discovery._build(metadata, "https://mcp.linear.app", "")


def test_discovery_walks_resource_metadata_then_server_metadata(monkeypatch):
    fetched = []

    def fake_get_json(url):
        fetched.append(url)
        if url.endswith("/.well-known/oauth-protected-resource/mcp"):
            return {"resource": "https://mcp.linear.app/mcp", "authorization_servers": ["https://mcp.linear.app"]}
        if url.endswith("/.well-known/oauth-authorization-server"):
            return METADATA
        raise discovery.HttpError(url, 404, "")

    monkeypatch.setattr(discovery, "get_json", fake_get_json)
    discovery.discover.cache_clear()

    server = discovery.discover(LINEAR)

    assert server.issuer == "https://mcp.linear.app"
    assert fetched[0] == "https://mcp.linear.app/.well-known/oauth-protected-resource/mcp"
    discovery.discover.cache_clear()


def test_an_explicit_authorization_server_skips_the_resource_lookup(monkeypatch):
    """A provider may pin its authorization server when discovery is unreliable."""
    provider = CimdProvider(
        key="custom",
        display_name="Custom",
        mcp_url="https://mcp.example.com/mcp",
        scope="read",
        summary="things",
        authorization_server="https://auth.example.com",
    )
    fetched = []

    def fake_get_json(url):
        fetched.append(url)
        if url == "https://auth.example.com/.well-known/oauth-authorization-server":
            return {**METADATA, "issuer": "https://auth.example.com"}
        raise discovery.HttpError(url, 404, "")

    monkeypatch.setattr(discovery, "get_json", fake_get_json)
    discovery.discover.cache_clear()

    server = discovery.discover(provider)

    assert server.issuer == "https://auth.example.com"
    # No protected resource lookup happened at all.
    assert all("oauth-protected-resource" not in url for url in fetched)
    discovery.discover.cache_clear()


def test_registry_entries_are_https_and_uniquely_named():
    for provider in (LINEAR, NOTION):
        assert provider.mcp_url.startswith("https://")
        assert provider.tool_name == f"use_{provider.key}"


def test_requests_send_an_explicit_user_agent(monkeypatch):
    """Notion's CDN answers 403 to urllib's default "Python-urllib/3.x" User-Agent."""
    from cimd import _http

    seen = {}

    class FakeResponse:
        status = 200

        def read(self, *args):
            return b'{"ok": true}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=None):
        seen["user_agent"] = request.get_header("User-agent")
        return FakeResponse()

    monkeypatch.setattr(_http.urllib.request, "urlopen", fake_urlopen)

    assert _http.get_json("https://example.com/.well-known/oauth-authorization-server") == {"ok": True}
    assert seen["user_agent"] == _http.USER_AGENT
    assert "urllib" not in seen["user_agent"]
