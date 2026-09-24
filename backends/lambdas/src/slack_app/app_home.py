"""Builds and publishes the Slack App Home tab (see GitHub issue #10).

Shared by two callers:
  * agent_worker.py  -- the `app_home_opened` event, queued by slack_events.py.
  * oauth_callback.py -- after a connect started from Home finishes, so the tab
    reflects the new status immediately instead of waiting for the user to reopen it.

Status comes from agent_client.check_connections(), which asks the agent runtime's
non-LLM "connections" mode -- see main.py in the agent for why that's live truth
rather than a copy we'd have to keep in sync.
"""

import logging
import time

from slack_app import agent_client
from slack_app.config import public_base_url
from slack_app.identity import runtime_user_id
from slack_app.pending_auth import TTL_SECONDS, PendingAuth, new_nonce, pending_auth_store
from slack_app.slack import slack_client

logger = logging.getLogger(__name__)


def publish_app_home(team_id: str, slack_user: str) -> None:
    user_id = runtime_user_id(team_id, slack_user)
    try:
        result = agent_client.check_connections(user_id)
    except Exception:
        logger.exception("Connection check failed for App Home")
        _publish(slack_user, _error_view())
        return

    providers = (result.get("connections") or {}).get("providers") or []
    _publish(slack_user, {"type": "home", "blocks": _blocks(providers, team_id, user_id, slack_user)})


def _publish(slack_user: str, view: dict) -> None:
    try:
        slack_client().views_publish(user_id=slack_user, view=view)
    except Exception:
        logger.exception("Failed to publish App Home view")


def _blocks(providers: list[dict], team_id: str, user_id: str, slack_user: str) -> list[dict]:
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "Connected accounts"}},
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "Connect an account to let the bot use it on your behalf."}],
        },
        {"type": "divider"},
    ]
    if not providers:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "No providers are configured."}})
        return blocks

    for provider in providers:
        blocks.append(_provider_block(provider, team_id, user_id, slack_user))
    return blocks


def _provider_block(provider: dict, team_id: str, user_id: str, slack_user: str) -> dict:
    name = provider.get("displayName") or provider.get("key")
    if provider.get("connected"):
        return {"type": "section", "text": {"type": "mrkdwn", "text": f"*{name}*\n:large_green_circle: Connected"}}

    section = {"type": "section", "text": {"type": "mrkdwn", "text": f"*{name}*\n:white_circle: Not connected"}}
    link = _connect_link(provider, team_id, user_id, slack_user)
    if link:
        section["accessory"] = {
            "type": "button",
            "text": {"type": "plain_text", "text": f"Connect {name}"},
            "url": link,
            "style": "primary",
        }
    return section


def _connect_link(provider: dict, team_id: str, user_id: str, slack_user: str) -> str | None:
    if not provider.get("authorizationUrl"):
        return None

    pending = PendingAuth(
        nonce=new_nonce(),
        runtime_user_id=user_id,
        provider=provider.get("displayName") or provider.get("key"),
        session_uri=provider.get("sessionUri") or "",
        authorization_url=provider["authorizationUrl"],
        channel="",
        slack_user=slack_user,
        thread_ts="",
        expires_at=int(time.time()) + TTL_SECONDS,
        cimd=provider.get("cimd"),
        team_id=team_id,
    )
    pending_auth_store().put(pending)
    return f"{public_base_url()}/oauth2/start?nonce={pending.nonce}"


def _error_view() -> dict:
    return {
        "type": "home",
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "⚠️ Couldn't load your connected accounts. Try reopening this tab."},
            }
        ],
    }
