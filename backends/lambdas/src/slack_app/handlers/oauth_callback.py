"""GET /oauth2/start and GET /oauth2/callback — OAuth2 session binding for AgentCore Identity.

See https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/oauth2-authorization-url-session-binding.html
"""

import hmac
import logging
import time
from functools import lru_cache

import boto3

from slack_app.apigw import cookie, html_response, query_param, redirect
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
    if path.endswith("/oauth2/start"):
        return start(event)
    if path.endswith("/oauth2/callback"):
        return callback(event)
    return _page(404, "Not found", "")


def start(event: dict) -> dict:
    nonce = query_param(event, "nonce")
    pending = pending_auth_store().get(nonce) if nonce else None
    if not pending or pending.expired():
        return _page(400, "Link expired", "This link has expired or was already used. Ask the bot again for a new one.")

    max_age = max(1, pending.expires_at - int(time.time()))
    return redirect(pending.authorization_url, cookies=[_cookie(nonce, max_age)])


def callback(event: dict) -> dict:
    session_id = query_param(event, "session_id")
    nonce = cookie(event, COOKIE_NAME)
    if not session_id or not nonce:
        return _page(400, "Sign-in not recognised", "Please start again from the link the bot sent you in Slack.")

    store = pending_auth_store()
    pending = store.get(nonce)
    # Session binding: the browser finishing consent must be the one that opened
    # the user's private link, and must be finishing *that* user's session.
    if not pending or pending.expired() or not hmac.compare_digest(pending.session_uri, session_id):
        logger.warning("OAuth callback rejected: session binding mismatch")
        return _page(403, "Sign-in not recognised", "This sign-in doesn't match your link. Ask the bot again.")

    if store.consume(nonce) is None:
        return _page(400, "Link already used", "This link was already used. Ask the bot again if needed.")

    try:
        _identity().complete_resource_token_auth(
            sessionUri=session_id,
            userIdentifier={"userId": pending.runtime_user_id},
        )
    except Exception:
        logger.exception("CompleteResourceTokenAuth failed")
        return _page(502, "Something went wrong", "We couldn't finish connecting your account. Please try again.")

    _notify(pending)
    return _page(
        200, f"{pending.provider} connected ✅", "You can close this tab and ask the bot again in Slack.", clear_cookie=True
    )


def _notify(pending) -> None:
    try:
        slack_client().chat_postEphemeral(
            channel=pending.channel,
            user=pending.slack_user,
            thread_ts=pending.thread_ts,
            text=f"✅ {pending.provider} connected. Ask me your question again.",
        )
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
