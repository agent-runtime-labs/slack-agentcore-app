"""Configuration loaded from environment variables (and Secrets Manager in AWS)."""

import json
import os
from dataclasses import dataclass
from functools import lru_cache

import boto3


@dataclass(frozen=True)
class SlackCredentials:
    bot_token: str
    signing_secret: str


def is_local() -> bool:
    """True when running under Tilt (local k8s) instead of AWS Lambda."""
    return os.getenv("APP_ENV", "aws") == "local"


def slack_dry_run() -> bool:
    """Log Slack API calls instead of sending them (local testing without a workspace)."""
    return os.getenv("SLACK_DRY_RUN", "false").lower() == "true"


def cookie_secure() -> bool:
    return os.getenv("COOKIE_SECURE", "true").lower() == "true"


def assistant_name() -> str:
    """The bot's display name in Slack (docs/slack-app-manifest.yaml), so it can be told apart from people's names."""
    return os.getenv("ASSISTANT_NAME", "AgentCore Assistant")


def public_base_url() -> str:
    """Base URL users' browsers use to reach the OAuth endpoints (API Gateway or localhost)."""
    return os.environ["PUBLIC_BASE_URL"].rstrip("/")


@lru_cache(maxsize=1)
def slack_credentials() -> SlackCredentials:
    """Slack secrets. Cached per Lambda container; redeploy or wait for recycle after rotation."""
    secret_arn = os.getenv("SLACK_SECRET_ARN")
    if secret_arn:
        raw = boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)["SecretString"]
        data = json.loads(raw)
        return SlackCredentials(bot_token=data["bot_token"], signing_secret=data["signing_secret"])
    return SlackCredentials(
        bot_token=os.getenv("SLACK_BOT_TOKEN", ""),
        signing_secret=os.environ["SLACK_SIGNING_SECRET"],
    )
