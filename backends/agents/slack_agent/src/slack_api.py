"""The agent's own, minimal access to Slack: the bot token and a Web API caller.

The agent posts progress to the placeholder message (slack_progress.py) and downloads
files people attached (slack_files.py) itself, rather than routing either through the
slack-app Lambdas. Both use the same bot token, from Secrets Manager in AWS or the
environment under Tilt.
"""

import json
import os
import urllib.parse
import urllib.request
from functools import lru_cache

import boto3

API_BASE_URL = "https://slack.com/api/"
TIMEOUT_SECONDS = 5


@lru_cache(maxsize=1)
def bot_token() -> str:
    """The bot token, or "" if none is configured (callers then skip Slack quietly)."""
    secret_arn = os.getenv("SLACK_SECRET_ARN")
    if secret_arn:
        raw = boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)["SecretString"]
        return json.loads(raw)["bot_token"]
    return os.getenv("SLACK_BOT_TOKEN", "")


def call(method: str, params: dict[str, str]) -> dict:
    """GET a read method such as files.info. Returns Slack's JSON, whether "ok" or not."""
    url = API_BASE_URL + method + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {bot_token()}"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # nosec B310 - hardcoded https URL
        return json.loads(response.read())
