"""Write side of the CIMD token table.

The agent reads and refreshes these items
(backends/agents/slack_agent/src/cimd/tokens.py); this Lambda only ever writes the first
one, right after a user consents. The two deployment units ship as separate images, so
the item shape is a contract -- docs/cimd-providers.md holds the canonical schema:

    user_id                    (HASH)  AgentCore runtime user id, "slack-<team>-<user>"
    provider                   (RANGE) registry key, e.g. "linear"
    access_token, refresh_token        secrets -- never logged
    access_token_expires_at            epoch seconds
    scope, issuer                      what was granted, and by whom
    updated_at                         epoch seconds
    ttl                                epoch seconds; DynamoDB drops idle connections

Local development has no table, so `CIMD_TOKEN_TABLE` being unset simply turns the CIMD
tools off in the agent -- consent can then never be started, and this is never reached.
"""

import os
import time
from functools import lru_cache

import boto3

DEFAULT_CONNECTION_TTL_DAYS = 90
DEFAULT_EXPIRES_IN = 3600


@lru_cache(maxsize=1)
def _table():
    name = os.environ["CIMD_TOKEN_TABLE"]
    return boto3.resource("dynamodb").Table(name)


def connection_ttl_days() -> int:
    return int(os.getenv("CIMD_CONNECTION_TTL_DAYS", DEFAULT_CONNECTION_TTL_DAYS))


def store_tokens(user_id: str, cimd: dict, token_response: dict) -> None:
    """Persist a freshly minted connection for (user, provider)."""
    now = int(time.time())
    expires_in = int(token_response.get("expires_in") or DEFAULT_EXPIRES_IN)

    _table().put_item(
        Item={
            "user_id": user_id,
            "provider": cimd["provider"],
            "access_token": token_response["access_token"],
            "refresh_token": token_response.get("refresh_token", ""),
            "access_token_expires_at": now + expires_in,
            # What the server actually granted, which may be narrower than we asked for.
            "scope": token_response.get("scope", cimd.get("scope", "")),
            "issuer": cimd.get("issuer", ""),
            "updated_at": now,
            "ttl": now + connection_ttl_days() * 86400,
        }
    )
