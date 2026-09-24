"""LinkedIn tool backed by AgentCore Identity (OAuth2 authorization code / 3-legged flow).

Tokens live in the AgentCore token vault keyed by (workload identity, user ID).
The workload access token we pass in already encodes the user, so every Slack
user gets their own LinkedIn token.
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

USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
SCOPES = ["openid", "profile", "email"]
AUTH_REQUIRED_MESSAGE = (
    "AUTHORIZATION_REQUIRED: the user has not connected LinkedIn yet. A private connect link "
    "has been sent to them. Tell them to connect and then ask again. Do not guess profile data."
)


@lru_cache(maxsize=1)
def _identity():
    return boto3.client("bedrock-agentcore", region_name=config.AWS_REGION)


def workload_token_provider(context_token: str | None, user_id: str) -> Callable[[], str]:
    """Returns a lazy getter so we only call Identity when the tool is actually used."""

    def get() -> str:
        if context_token:
            return context_token
        if not config.LOCAL_WORKLOAD_NAME:
            raise RuntimeError("No workload access token in the request (was runtimeUserId set?)")
        response = _identity().get_workload_access_token_for_user_id(
            workloadName=config.LOCAL_WORKLOAD_NAME, userId=user_id
        )
        return response["workloadAccessToken"]

    return get


def fetch_token(workload_token: str, force: bool = False) -> dict:
    """Either {'accessToken': ...} or {'authorizationUrl': ..., 'sessionUri': ...}."""
    return _identity().get_resource_oauth2_token(
        workloadIdentityToken=workload_token,
        resourceCredentialProviderName=config.LINKEDIN_PROVIDER_NAME,
        scopes=SCOPES,
        oauth2Flow="USER_FEDERATION",
        resourceOauth2ReturnUrl=config.OAUTH2_RETURN_URL,
        forceAuthentication=force,
    )


def call_userinfo(access_token: str) -> tuple[int, dict]:
    request = urllib.request.Request(USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # nosec B310 - fixed https URL
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as err:
        return err.code, {"error": err.reason}


def check_connection(get_workload_token: Callable[[], str]) -> dict:
    """Live status check, no LLM involved: does the user have a usable LinkedIn token?

    Used by the App Home tab (main.py's "connections" mode) to show connect status
    without spending a chat turn. Shares fetch_token with the tool above, so the
    result is exactly what get_my_linkedin_profile would see.
    """
    try:
        token = fetch_token(get_workload_token())
    except Exception:
        logger.exception("LinkedIn connection check failed")
        return {"connected": False, "authorizationUrl": None, "sessionUri": None}

    if token.get("accessToken"):
        return {"connected": True, "authorizationUrl": None, "sessionUri": None}
    return {
        "connected": False,
        "authorizationUrl": token.get("authorizationUrl"),
        "sessionUri": token.get("sessionUri"),
    }


def build_linkedin_tool(get_workload_token: Callable[[], str], auth_state: AuthState):
    @tool
    def get_my_linkedin_profile() -> str:
        """Get the LinkedIn profile (name, email, picture, locale) of the Slack user who is asking.

        Use this whenever the user asks about their own LinkedIn account or profile.
        """
        try:
            workload_token = get_workload_token()
            token = fetch_token(workload_token)

            if token.get("accessToken"):
                status, profile = call_userinfo(token["accessToken"])
                if status == 200:
                    return json.dumps(profile)
                if status != 401:
                    return f"ERROR: LinkedIn returned HTTP {status}"
                # Stored token was revoked or expired: ask the user to consent again.
                token = fetch_token(workload_token, force=True)

            if token.get("authorizationUrl"):
                auth_state.provider = "LinkedIn"
                auth_state.authorization_url = token["authorizationUrl"]
                auth_state.session_uri = token.get("sessionUri")
                return AUTH_REQUIRED_MESSAGE
        except Exception:
            logger.exception("LinkedIn tool failed")
            return "ERROR: could not reach LinkedIn right now"

        return "ERROR: could not obtain a LinkedIn access token"

    return get_my_linkedin_profile
