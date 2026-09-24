"""The browser-facing OAuth endpoints.

    GET /oauth2/start                  nonce -> cookie -> redirect to the provider
    GET /oauth2/callback               finish consent (two flavours, see below)
    GET /oauth2/client-metadata.json   our CIMD client document (public, cacheable)

Two consent flows land on the same callback and are told apart by the pending record:

  * **AgentCore Identity** (LinkedIn, GitHub) -- AWS ran the OAuth exchange and returns
    with `?session_id=`. We only confirm that the session belongs to the Slack user who
    opened the link, then call CompleteResourceTokenAuth.
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/oauth2-authorization-url-session-binding.html

  * **CIMD** (Linear, Notion, ...) -- we are the OAuth client, so the provider returns
    with `?code=&state=` and we perform the token exchange ourselves.

Both are bound to the user the same way: the single-use nonce in an HttpOnly cookie must
match the pending record created when the bot sent the private link. The CIMD path adds
the OAuth `state` check and, where the server supports it, the `iss` check from RFC 9207.
"""

import hmac
import logging
import time
from functools import lru_cache

import boto3

from slack_app import app_home, cimd_client, cimd_tokens
from slack_app.apigw import cookie, html_response, json_response, query_param, redirect
from slack_app.config import cookie_secure
from slack_app.pending_auth import pending_auth_store
from slack_app.slack import slack_client

logger = logging.getLogger(__name__)

COOKIE_NAME = "slack_agent_oauth"
COOKIE_PATH = "/oauth2"


@lru_cache(maxsize=1)
def _identity():
    return boto3.client("bedrock-agentcore")


def handler(event: dict, context) -> dict:
    path = event.get("rawPath", "")
    if path.endswith(cimd_client.METADATA_PATH):
        return client_metadata(event)
    if path.endswith("/oauth2/start"):
        return start(event)
    if path.endswith("/oauth2/callback"):
        return callback(event)
    return _page(404, "Not found", "")


def client_metadata(event: dict) -> dict:
    """Serve the client ID metadata document.

    Authorization servers fetch this unauthenticated, from their own infrastructure, the
    first time a user consents -- so it must stay public and must never vary per caller.
    """
    return json_response(200, cimd_client.client_metadata_document(), {"Cache-Control": cimd_client.CACHE_CONTROL})


def start(event: dict) -> dict:
    nonce = query_param(event, "nonce")
    pending = pending_auth_store().get(nonce) if nonce else None
    if not pending or pending.expired():
        return _page(400, "Link expired", "This link has expired or was already used. Ask the bot again for a new one.")

    max_age = max(1, pending.expires_at - int(time.time()))
    return redirect(pending.authorization_url, cookies=[_cookie(nonce, max_age)])


def callback(event: dict) -> dict:
    nonce = cookie(event, COOKIE_NAME)
    if not nonce:
        return _page(400, "Sign-in not recognised", "Please start again from the link the bot sent you in Slack.")

    store = pending_auth_store()
    pending = store.get(nonce)
    # Unknown or expired also covers a replayed callback: the record is consumed the
    # moment a consent completes, so a second visit finds nothing to finish.
    if not pending or pending.expired():
        logger.warning("OAuth callback rejected: no live pending record for this cookie")
        return _page(403, "Sign-in not recognised", "This sign-in doesn't match your link. Ask the bot again.")

    if pending.cimd:
        return _complete_cimd(event, store, pending)
    return _complete_agentcore(event, store, pending)


def _complete_agentcore(event: dict, store, pending) -> dict:
    session_id = query_param(event, "session_id")
    # Session binding: the browser finishing consent must be the one that opened
    # the user's private link, and must be finishing *that* user's session.
    if not session_id or not hmac.compare_digest(pending.session_uri, session_id):
        logger.warning("OAuth callback rejected: session binding mismatch")
        return _page(403, "Sign-in not recognised", "This sign-in doesn't match your link. Ask the bot again.")

    if store.consume(pending.nonce) is None:
        return _page(400, "Link already used", "This link was already used. Ask the bot again if needed.")

    try:
        _identity().complete_resource_token_auth(
            sessionUri=session_id,
            userIdentifier={"userId": pending.runtime_user_id},
        )
    except Exception:
        logger.exception("CompleteResourceTokenAuth failed")
        return _page(502, "Something went wrong", "We couldn't finish connecting your account. Please try again.")

    return _connected(pending)


def _complete_cimd(event: dict, store, pending) -> dict:
    """Finish an authorization-code + PKCE flow that we started ourselves."""
    cimd = pending.cimd

    error = query_param(event, "error")
    if error:
        # The user pressed "Cancel", or the server refused us. Burn the nonce either way.
        store.consume(pending.nonce)
        logger.info("%s consent returned error=%s", cimd["provider"], error)
        return _page(
            200,
            f"{pending.provider} not connected",
            "You can close this tab. Ask the bot again if you'd like to retry.",
            clear_cookie=True,
        )

    code = query_param(event, "code")
    state = query_param(event, "state")
    # The state ties this redirect to the authorization request we built, so a code from
    # any other flow (or injected by a third party) is rejected before it is spent.
    if not code or not state or not hmac.compare_digest(cimd["state"], state):
        logger.warning("%s callback rejected: state mismatch", cimd["provider"])
        return _page(403, "Sign-in not recognised", "This sign-in doesn't match your link. Ask the bot again.")

    # RFC 9207: servers that echo the issuer let us detect a mix-up between two
    # authorization servers. Absent is fine; wrong is not.
    issuer = query_param(event, "iss")
    if issuer and issuer != cimd.get("issuer"):
        logger.warning("%s callback rejected: unexpected issuer", cimd["provider"])
        return _page(403, "Sign-in not recognised", "This sign-in came from an unexpected place. Ask the bot again.")

    if store.consume(pending.nonce) is None:
        return _page(400, "Link already used", "This link was already used. Ask the bot again if needed.")

    try:
        tokens = cimd_client.exchange_code(cimd, code)
        cimd_tokens.store_tokens(pending.runtime_user_id, cimd, tokens)
    except Exception:
        # Nothing from the exchange goes into the log line: bodies can echo the code.
        logger.exception("CIMD token exchange failed for %s", cimd["provider"])
        return _page(502, "Something went wrong", "We couldn't finish connecting your account. Please try again.")

    return _connected(pending)


def _connected(pending) -> dict:
    _notify(pending)
    return _page(
        200, f"{pending.provider} connected ✅", "You can close this tab and ask the bot again in Slack.", clear_cookie=True
    )


def _notify(pending) -> None:
    try:
        if pending.channel:
            slack_client().chat_postEphemeral(
                channel=pending.channel,
                user=pending.slack_user,
                thread_ts=pending.thread_ts,
                text=f"✅ {pending.provider} connected. Ask me your question again.",
            )
        else:
            # Started from the App Home tab, which has no channel/thread to post into --
            # republish it so the status flips to Connected right away.
            app_home.publish_app_home(pending.team_id, pending.slack_user)
    except Exception:
        logger.exception("Failed to notify Slack user")


def _cookie(value: str, max_age: int) -> str:
    parts = [f"{COOKIE_NAME}={value}", f"Path={COOKIE_PATH}", f"Max-Age={max_age}", "HttpOnly", "SameSite=Lax"]
    if cookie_secure():
        parts.append("Secure")
    return "; ".join(parts)


def _page(status: int, title: str, message: str, clear_cookie: bool = False) -> dict:
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{title}</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:32rem;margin:4rem auto;padding:0 1rem;color:#1d1c1d}"
        "h1{font-size:1.4rem}</style></head>"
        f"<body><h1>{title}</h1><p>{message}</p></body></html>"
    )
    cookies = [_cookie("", 0)] if clear_cookie else None
    return html_response(status, html, cookies=cookies)
