"""The two CIMD pieces that must live in the public Lambda: our client document and the
authorization-code exchange.

CIMD (draft-ietf-oauth-client-id-metadata-document, adopted by MCP as SEP-991) replaces
client registration with a URL. Our `client_id` is

    <PUBLIC_BASE_URL>/oauth2/client-metadata.json

and the authorization server fetches it to learn who we are. There is no client secret
anywhere in this app: the authorization request is bound to us by PKCE and by the exact
redirect URI listed in that document.

The agent builds the authorization URL (backends/agents/slack_agent/src/cimd/oauth.py)
and hands the callback everything needed to finish, as the `cimd` payload described in
docs/cimd-providers.md. This module is deliberately provider-agnostic -- it never names
Linear or Notion -- so adding a provider needs no change here.

Trust model: the `cimd` payload arrives from our own agent runtime, which only the worker
Lambda's role may invoke, and it is written to the pending-auth table before the user's
browser is ever redirected. The callback re-checks everything a browser could influence
(cookie nonce, state, iss) and refuses any endpoint that is not https.
"""

import json
import logging
import urllib.parse
import urllib.request

from slack_app.config import public_base_url

logger = logging.getLogger(__name__)

METADATA_PATH = "/oauth2/client-metadata.json"
CLIENT_NAME = "Slack AgentCore Assistant"
# Some token endpoints sit behind a CDN that answers 403 to urllib's default
# "Python-urllib/3.x" User-Agent, so send our own (mirrors cimd/_http.py in the agent).
USER_AGENT = "slack-agentcore-app/1.0 (+https://github.com/aws-samples/slack-agentcore-app)"
TIMEOUT_SECONDS = 10
MAX_RESPONSE_BYTES = 64 * 1024

# The draft recommends serving the document from a cacheable, stable URL and keeping it
# small (~5 KB). An hour is long enough to spare the authorization servers and short
# enough that redeploying to a new API Gateway URL is not painful.
CACHE_CONTROL = "public, max-age=3600"


def client_id() -> str:
    """Our OAuth client identifier -- the URL of the document below."""
    return f"{public_base_url()}{METADATA_PATH}"


def redirect_uri() -> str:
    """The one redirect URI we will ever use; also what AgentCore Identity returns to."""
    return f"{public_base_url()}/oauth2/callback"


def client_metadata_document() -> dict:
    """The document served at METADATA_PATH.

    `client_id` MUST equal the URL this is served from -- authorization servers check it,
    and a mismatch is how they detect a document copied from somebody else. `client_uri`
    is kept on the same origin for the same reason. `token_endpoint_auth_method: none`
    says we are a public client: no secret, PKCE only, exactly what the draft requires
    (section 4.1 forbids every shared-secret method).
    """
    return {
        "client_id": client_id(),
        "client_name": CLIENT_NAME,
        "client_uri": public_base_url(),
        "redirect_uris": [redirect_uri()],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "application_type": "web",
    }


def exchange_code(cimd: dict, code: str) -> dict:
    """Trade an authorization code for tokens (RFC 6749 section 4.1.3 + PKCE).

    `cimd` is the payload the agent stored in the pending-auth record. The code verifier
    proves we are the client that started this authorization: without it the code is
    useless to anyone who intercepts the redirect.
    """
    token_endpoint = cimd["tokenEndpoint"]
    if not token_endpoint.startswith("https://"):
        raise ValueError("token endpoint must be https")

    fields = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": cimd["redirectUri"],
        "client_id": cimd["clientId"],
        "code_verifier": cimd["codeVerifier"],
    }
    if cimd.get("resource"):
        # RFC 8707: keep the token bound to the MCP server it was requested for.
        fields["resource"] = cimd["resource"]

    request = urllib.request.Request(
        token_endpoint,
        data=urllib.parse.urlencode(fields).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # nosec B310 - https enforced above
        payload = json.loads(response.read(MAX_RESPONSE_BYTES))

    if not payload.get("access_token"):
        # Never log the payload itself; a token endpoint error body can echo the code.
        raise RuntimeError(f"{cimd['provider']} token response contained no access_token")
    logger.info("Exchanged authorization code for %s tokens", cimd["provider"])
    return payload
