"""The OAuth 2.1 authorization-code + PKCE flow, with a URL as the client_id.

What makes this CIMD rather than ordinary OAuth is one line: `client_id` is the HTTPS
URL of our client metadata document (served by the oauth_callback Lambda at
/oauth2/client-metadata.json). The authorization server fetches that URL, reads our
name and redirect_uris from it, and that is the whole registration -- no client secret
exists, so every request is authenticated by PKCE and the redirect URI instead.

This module owns the two halves the *agent* performs:

    consent_request()   build the authorization URL the user opens in a browser
    refresh()           trade a refresh token for a fresh access token

The third half -- turning the authorization code into tokens -- happens in the
oauth_callback Lambda, because that is where the browser lands
(backends/lambdas/src/slack_app/cimd_client.py).
"""

import base64
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import config
from cimd._http import post_form
from cimd.discovery import AuthorizationServer
from cimd.providers import CimdProvider
from cimd.tokens import StoredToken

logger = logging.getLogger(__name__)

# Access tokens are refreshed this many seconds before they actually expire, so a slow
# MCP round-trip can't start with a valid token and finish with an expired one.
EXPIRY_SKEW_SECONDS = 60
DEFAULT_EXPIRES_IN = 3600


@dataclass(frozen=True)
class ConsentRequest:
    """Everything needed to finish a consent that has not started yet.

    `authorization_url` goes to the user (via the Slack connect link). The rest travels
    agent -> agent_worker Lambda -> pending-auth table -> oauth_callback Lambda as the
    `authRequired.cimd` payload documented in docs/cimd-providers.md.
    """

    authorization_url: str
    state: str
    code_verifier: str
    provider: CimdProvider
    server: AuthorizationServer

    def as_payload(self) -> dict:
        """The handoff contract. Keys are camelCase to match the rest of the agent's JSON."""
        return {
            "provider": self.provider.key,
            "displayName": self.provider.display_name,
            "state": self.state,
            "codeVerifier": self.code_verifier,
            "clientId": config.CIMD_CLIENT_ID,
            "redirectUri": config.OAUTH2_RETURN_URL,
            "tokenEndpoint": self.server.token_endpoint,
            "issuer": self.server.issuer,
            "resource": self.server.resource,
            "scope": self.provider.scope,
        }


def consent_request(provider: CimdProvider, server: AuthorizationServer) -> ConsentRequest:
    """Build an authorization request for `provider`.

    `state` is a single-use random value the callback checks against the pending record,
    and the PKCE verifier never leaves our infrastructure -- only its SHA-256 hash travels
    through the user's browser.
    """
    if not config.CIMD_CLIENT_ID or not config.OAUTH2_RETURN_URL:
        raise RuntimeError("CIMD_CLIENT_ID and OAUTH2_RETURN_URL must be set to start a CIMD consent")

    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(32)

    params = {
        "response_type": "code",
        "client_id": config.CIMD_CLIENT_ID,
        "redirect_uri": config.OAUTH2_RETURN_URL,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if provider.scope:
        params["scope"] = provider.scope
    if server.resource:
        # RFC 8707: bind the token to this MCP server so it is useless anywhere else.
        params["resource"] = server.resource

    return ConsentRequest(
        authorization_url=f"{server.authorization_endpoint}?{urlencode(params)}",
        state=state,
        code_verifier=verifier,
        provider=provider,
        server=server,
    )


def refresh(provider: CimdProvider, server: AuthorizationServer, stored: StoredToken) -> StoredToken:
    """Exchange a refresh token for a new access token.

    A public client authenticates with nothing but its client_id URL, so the refresh
    token is the whole credential -- treat it like a password (tokens.py keeps it in an
    encrypted table and nothing ever logs it).
    """
    fields = {
        "grant_type": "refresh_token",
        "refresh_token": stored.refresh_token,
        "client_id": config.CIMD_CLIENT_ID,
    }
    if server.resource:
        fields["resource"] = server.resource

    response = post_form(server.token_endpoint, fields)
    logger.info("Refreshed %s access token", provider.key)
    return token_from_response(
        response,
        user_id=stored.user_id,
        provider_key=provider.key,
        issuer=server.issuer,
        # Servers may or may not rotate the refresh token; keep the old one if they don't.
        fallback_refresh_token=stored.refresh_token,
    )


def token_from_response(
    response: dict,
    *,
    user_id: str,
    provider_key: str,
    issuer: str,
    fallback_refresh_token: str = "",
) -> StoredToken:
    """Turn an RFC 6749 token response into the record we persist.

    Shared with the Lambda's code exchange by contract, not by import -- the two
    deployment units have separate images. Keep the field names in step with
    backends/lambdas/src/slack_app/cimd_tokens.py.
    """
    access_token = response.get("access_token")
    if not access_token:
        raise RuntimeError(f"Token response for {provider_key} contained no access_token")

    expires_in = int(response.get("expires_in") or DEFAULT_EXPIRES_IN)
    return StoredToken(
        user_id=user_id,
        provider=provider_key,
        access_token=access_token,
        refresh_token=response.get("refresh_token") or fallback_refresh_token,
        access_token_expires_at=int(time.time()) + expires_in,
        scope=response.get("scope", ""),
        issuer=issuer,
    )
