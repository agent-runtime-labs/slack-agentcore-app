"""Find a remote MCP server's authorization server, the way the MCP spec says to.

    MCP endpoint  --RFC 9728-->  protected resource metadata  -->  issuer
    issuer        --RFC 8414-->  authorization server metadata -->  endpoints

Both well-known lookups insert the well-known segment *between* the host and the path
(https://host/mcp -> https://host/.well-known/oauth-protected-resource/mcp), with the
path-less form as a fallback for servers that only publish the root document.

Results are cached for the life of the container: this metadata changes about as often
as the vendor redeploys, and an AgentCore Runtime session is minutes long.
"""

import logging
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlsplit, urlunsplit

from cimd._http import HttpError, get_json, require_https
from cimd.providers import CimdProvider

logger = logging.getLogger(__name__)

PROTECTED_RESOURCE = "/.well-known/oauth-protected-resource"
AUTHORIZATION_SERVER = "/.well-known/oauth-authorization-server"
OPENID_CONFIGURATION = "/.well-known/openid-configuration"


class CimdUnsupported(RuntimeError):
    """The server's authorization server explicitly says it does not accept CIMD clients."""


@dataclass(frozen=True)
class AuthorizationServer:
    """The subset of RFC 8414 metadata this flow needs."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    resource: str
    """Canonical resource identifier to send as RFC 8707 `resource`, binding the token to
    this MCP server so it can't be replayed elsewhere. Empty when the server publishes none."""
    scopes_supported: tuple[str, ...]
    returns_iss: bool
    """True when the server echoes `iss` on the authorization response (RFC 9207), which
    lets the callback detect a mix-up between two authorization servers."""


@lru_cache(maxsize=16)
def discover(provider: CimdProvider) -> AuthorizationServer:
    """Resolve `provider`'s authorization server. Raises on anything unusable."""
    resource, issuer = _resource_and_issuer(provider)
    metadata = _authorization_server_metadata(issuer)
    server = _build(metadata, issuer, resource)
    logger.info("Discovered %s authorization server: %s", provider.key, server.issuer)
    return server


def _resource_and_issuer(provider: CimdProvider) -> tuple[str, str]:
    """Protected resource metadata (RFC 9728) for the MCP endpoint."""
    require_https(provider.mcp_url, "MCP server URL")
    if provider.authorization_server:
        # Explicit override: skip the lookup entirely.
        return provider.mcp_url, require_https(provider.authorization_server, "Authorization server")

    metadata = _first_available(_well_known_urls(provider.mcp_url, PROTECTED_RESOURCE))
    if not metadata:
        # No protected resource document: assume the MCP server's own origin is the
        # authorization server, which is how several first-party servers are deployed.
        logger.warning("%s publishes no protected resource metadata; assuming origin", provider.key)
        return provider.mcp_url, _origin(provider.mcp_url)

    issuers = metadata.get("authorization_servers") or []
    if not issuers:
        raise RuntimeError(f"{provider.key}: protected resource metadata lists no authorization_servers")
    # MCP clients may pick any listed server; the first is the vendor's preferred one.
    return metadata.get("resource") or provider.mcp_url, require_https(issuers[0], "Authorization server")


def _authorization_server_metadata(issuer: str) -> dict:
    candidates = _well_known_urls(issuer, AUTHORIZATION_SERVER) + _well_known_urls(issuer, OPENID_CONFIGURATION)
    metadata = _first_available(candidates)
    if not metadata:
        raise RuntimeError(f"No authorization server metadata under {issuer}")
    return metadata


def _build(metadata: dict, issuer: str, resource: str) -> AuthorizationServer:
    # Explicit `false` means the server would reject a URL client_id, so fail with a clear
    # message instead of a confusing invalid_client later. A missing flag is only a warning:
    # some servers accept CIMD before they advertise it.
    supported = metadata.get("client_id_metadata_document_supported")
    if supported is False:
        raise CimdUnsupported(
            f"{issuer} does not support client ID metadata documents "
            "(it still requires Dynamic Client Registration or a pre-registered client)"
        )
    if supported is None:
        logger.warning("%s does not advertise client_id_metadata_document_supported; trying anyway", issuer)

    # We always send PKCE; a server without S256 cannot be used safely as a public client.
    methods = metadata.get("code_challenge_methods_supported") or []
    if "S256" not in methods:
        raise RuntimeError(f"{issuer} does not support PKCE S256 (advertises {methods})")

    return AuthorizationServer(
        issuer=metadata.get("issuer") or issuer,
        authorization_endpoint=require_https(metadata["authorization_endpoint"], "Authorization endpoint"),
        token_endpoint=require_https(metadata["token_endpoint"], "Token endpoint"),
        resource=resource,
        scopes_supported=tuple(metadata.get("scopes_supported") or ()),
        returns_iss=bool(metadata.get("authorization_response_iss_parameter_supported")),
    )


def _well_known_urls(url: str, suffix: str) -> list[str]:
    """[<host>/.well-known/<suffix><path>, <host>/.well-known/<suffix>] -- most specific first."""
    parts = urlsplit(url)
    path = parts.path.rstrip("/")
    urls = [urlunsplit((parts.scheme, parts.netloc, f"{suffix}{path}", "", ""))]
    if path:
        urls.append(urlunsplit((parts.scheme, parts.netloc, suffix, "", "")))
    return urls


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def _first_available(urls: list[str]) -> dict | None:
    for url in urls:
        try:
            return get_json(url)
        except HttpError as err:
            logger.debug("Discovery: %s -> HTTP %s", url, err.status)
        except Exception:  # noqa: BLE001 - network hiccup on one candidate shouldn't end the walk
            logger.debug("Discovery: %s unreachable", url, exc_info=True)
    return None
