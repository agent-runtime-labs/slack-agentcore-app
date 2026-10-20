"""Browser login stages credentials; only a subsequent signed Slack action activates them."""

import hmac
import logging

from slack_app.apigw import query_param
from slack_app.k8s_auth.protocol import Protocol
from slack_app.k8s_auth.settings import Settings
from slack_app.k8s_auth.store import Store
from slack_app.slack import slack_client

logger = logging.getLogger(__name__)


def complete(event, pending_store, pending):
    from slack_app.handlers.oauth_callback import _page

    payload = pending.kubernetes
    state = query_param(event, "state")
    if not state or not hmac.compare_digest(payload.get("state", ""), state):
        return _page(403, "Sign-in not recognised", "Start again from your private Slack link.")
    settings = Settings.load()
    issuer = query_param(event, "iss")
    if (not settings or payload.get("fingerprint") != settings.fingerprint
            or (issuer and issuer != settings.issuer)):
        return _page(403, "Sign-in not recognised", "Start again from your private Slack link.")
    if query_param(event, "error"):
        pending_store.consume(pending.nonce)
        return _page(200, "Kubernetes not connected", "Ask Mitra for a new link when ready.", clear_cookie=True)
    code = query_param(event, "code")
    if not code:
        return _page(403, "Sign-in not recognised", "Start again from your private Slack link.")
    if pending_store.consume(pending.nonce) is None:
        return _page(400, "Link already used", "Ask Mitra for a new link.")
    try:
        session = Protocol(settings).exchange_code(payload, code)
        confirmation = Store(settings).stage(pending.runtime_user_id, session, pending.resume_job)
        username = session["username"]
        slack_client().chat_postEphemeral(channel=pending.channel, user=pending.slack_user,
            thread_ts=pending.thread_ts, text="Confirm your Kubernetes identity before Mitra can use it.",
            blocks=[{"type": "section", "text": {"type": "plain_text",
                "text": f"Connect Kubernetes identity {username} to your Slack account? Only confirm if this is you."}},
                {"type": "actions", "elements": [{"type": "button", "action_id": "k8s_confirm",
                    "text": {"type": "plain_text", "text": "Confirm my identity"},
                    "style": "primary", "value": confirmation}]}])
    except Exception:
        # Never log remote exception text, tokens, authorization codes or Slack payloads.
        logger.warning("Kubernetes login could not be staged")
        return _page(502, "Connection failed", "Ask Mitra for a new link and try again.", clear_cookie=True)
    return _page(200, "Confirm in Slack", "Return to Slack and confirm your Kubernetes identity using the private button.",
                 clear_cookie=True)
