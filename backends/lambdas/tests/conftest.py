import hashlib
import hmac
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

SIGNING_SECRET = "test-signing-secret"


@pytest.fixture(autouse=True)
def local_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("SLACK_DRY_RUN", "true")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", SIGNING_SECRET)
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:8081")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    for name in (
        "PROCESSING_QUEUE_URL",
        "PENDING_AUTH_TABLE",
        "ENGAGED_THREADS_TABLE",
        "SLACK_SECRET_ARN",
        "AGENT_LOCAL_URL",
    ):
        monkeypatch.delenv(name, raising=False)

    from slack_app import config, engaged_threads, pending_auth, slack

    config.slack_credentials.cache_clear()
    slack.slack_client.cache_clear()
    slack._auth_test_user_id.cache_clear()
    pending_auth.pending_auth_store.cache_clear()
    engaged_threads.engaged_thread_store.cache_clear()
    yield


def signed_event(payload: dict, *, timestamp: int | None = None, secret: str = SIGNING_SECRET, extra_headers=None):
    body = json.dumps(payload)
    ts = str(timestamp or int(time.time()))
    digest = hmac.new(secret.encode(), f"v0:{ts}:{body}".encode(), hashlib.sha256).hexdigest()
    headers = {"x-slack-request-timestamp": ts, "x-slack-signature": f"v0={digest}"}
    headers.update(extra_headers or {})
    return {"rawPath": "/slack/events", "headers": headers, "body": body, "isBase64Encoded": False}


def mention_payload(text="<@UBOT> what's my LinkedIn name?", **event_overrides):
    event = {
        "type": "app_mention",
        "user": "UALICE",
        "text": text,
        "channel": "C123",
        "ts": "1726500000.000100",
        "team": "T999",
    }
    event.update(event_overrides)
    return {
        "type": "event_callback",
        "team_id": "T999",
        "event_id": "Ev1",
        "event": event,
        "authorizations": [{"team_id": "T999", "user_id": "UBOT", "is_bot": True}],
    }


def channel_message_payload(text="does anyone know how to connect Notion?", **event_overrides):
    """A plain channel message with no @mention of the bot (Slack's message.channels event)."""
    return mention_payload(text=text, **{"type": "message", "channel_type": "channel", **event_overrides})
