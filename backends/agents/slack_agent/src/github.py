"""GitHub tool backed by AgentCore Identity (OAuth2 authorization code / 3-legged flow).

Tokens live in the AgentCore token vault keyed by (workload identity, user ID).
The workload access token we pass in already encodes the user, so every Slack
user gets their own GitHub token.
"""

import json
import logging
import urllib.error
import urllib.request
from functools import lru_cache
from typing import Callable

import boto3
from strands import tool

import config
from auth_state import AuthState

logger = logging.getLogger(__name__)

USER_URL = "https://api.github.com/user"
SCOPES = ["read:user"]
AUTH_REQUIRED_MESSAGE = (
    "AUTHORIZATION_REQUIRED: the user has not connected GitHub yet. A private connect link "
    "has been sent to them. Tell them to connect and then ask again. Do not guess profile data."
)


@lru_cache(maxsize=1)
def _identity():
    return boto3.client("bedrock-agentcore", region_name=config.AWS_REGION)


def fetch_token(workload_token: str, force: bool = False) -> dict:
    """Either {'accessToken': ...} or {'authorizationUrl': ..., 'sessionUri': ...}."""
    return _identity().get_resource_oauth2_token(
        workloadIdentityToken=workload_token,
        resourceCredentialProviderName=config.GITHUB_PROVIDER_NAME,
        scopes=SCOPES,
        oauth2Flow="USER_FEDERATION",
        resourceOauth2ReturnUrl=config.OAUTH2_RETURN_URL,
        forceAuthentication=force,
    )


def call_user(access_token: str) -> tuple[int, dict]:
    request = urllib.request.Request(
        USER_URL,
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # nosec B310 - fixed https URL
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as err:
        return err.code, {"error": err.reason}


def build_github_tool(get_workload_token: Callable[[], str], auth_state: AuthState):
    @tool
    def get_my_github_profile() -> str:
        """Get the GitHub profile (login, name, bio, public repo count) of the Slack user who is asking.

        Use this whenever the user asks about their own GitHub account or profile.
        """
        try:
            workload_token = get_workload_token()
            token = fetch_token(workload_token)

            if token.get("accessToken"):
                status, profile = call_user(token["accessToken"])
                if status == 200:
                    return json.dumps(profile)
                if status != 401:
                    return f"ERROR: GitHub returned HTTP {status}"
                # Stored token was revoked or expired: ask the user to consent again.
                token = fetch_token(workload_token, force=True)

            if token.get("authorizationUrl"):
                auth_state.provider = "GitHub"
                auth_state.authorization_url = token["authorizationUrl"]
                auth_state.session_uri = token.get("sessionUri")
                return AUTH_REQUIRED_MESSAGE
        except Exception:
            logger.exception("GitHub tool failed")
            return "ERROR: could not reach GitHub right now"

        return "ERROR: could not obtain a GitHub access token"

    return get_my_github_profile
