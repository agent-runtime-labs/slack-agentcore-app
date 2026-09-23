"""Per-user OAuth tokens for CIMD providers, kept in DynamoDB.

With AgentCore Identity, AWS owns the token vault. A CIMD client has no vault, so this
table *is* the vault and the same care applies:

  * One item per (Slack user, provider): hash key `user_id`, sort key `provider`.
  * The table is encrypted at rest and reachable only by the agent runtime role (read,
    write) and the oauth_callback Lambda role (write) -- see infra-as-code/tf-app.
  * Nothing here is ever logged. Tokens are returned to callers, never to log records.
  * `ttl` expires the whole connection (default 90 days) so an abandoned Slack user's
    refresh token does not live forever. It is deliberately *not* the access token's
    expiry -- that would delete the refresh token an hour after consent.

The oauth_callback Lambda writes the first item of a connection with the same field
names (backends/lambdas/src/slack_app/cimd_tokens.py); the two are separate images, so
the schema is a contract rather than shared code. docs/cimd-providers.md is the
authority if they ever disagree.
"""

import logging
import time
from dataclasses import dataclass
from functools import lru_cache

import boto3

import config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StoredToken:
    user_id: str
    provider: str
    access_token: str
    refresh_token: str
    access_token_expires_at: int
    scope: str
    issuer: str

    def expired(self, skew_seconds: int = 60, now: float | None = None) -> bool:
        return (now or time.time()) >= self.access_token_expires_at - skew_seconds

    def as_item(self, connection_ttl_days: int) -> dict:
        item = {k: v for k, v in vars(self).items()}
        item["updated_at"] = int(time.time())
        item["ttl"] = int(time.time()) + connection_ttl_days * 86400
        return item


class DynamoTokenStore:
    def __init__(self, table_name: str, connection_ttl_days: int):
        self._table = boto3.resource("dynamodb", region_name=config.AWS_REGION).Table(table_name)
        self._connection_ttl_days = connection_ttl_days

    def get(self, user_id: str, provider: str) -> StoredToken | None:
        # Consistent read: the user may ask again the instant after consent completes.
        item = self._table.get_item(
            Key={"user_id": user_id, "provider": provider}, ConsistentRead=True
        ).get("Item")
        return _from_item(item) if item else None

    def put(self, token: StoredToken) -> None:
        self._table.put_item(Item=token.as_item(self._connection_ttl_days))

    def delete(self, user_id: str, provider: str) -> None:
        """Drop a connection whose tokens no longer work, so the user is asked to reconnect."""
        self._table.delete_item(Key={"user_id": user_id, "provider": provider})


def _from_item(item: dict) -> StoredToken:
    return StoredToken(
        user_id=item["user_id"],
        provider=item["provider"],
        access_token=item["access_token"],
        refresh_token=item.get("refresh_token", ""),
        access_token_expires_at=int(item.get("access_token_expires_at", 0)),  # DynamoDB returns Decimal
        scope=item.get("scope", ""),
        issuer=item.get("issuer", ""),
    )


@lru_cache(maxsize=1)
def token_store() -> DynamoTokenStore:
    if not config.CIMD_TOKEN_TABLE:
        raise RuntimeError("CIMD_TOKEN_TABLE is not set")
    return DynamoTokenStore(config.CIMD_TOKEN_TABLE, config.CIMD_CONNECTION_TTL_DAYS)
