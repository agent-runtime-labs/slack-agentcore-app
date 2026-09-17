"""GitHub tool backed by AgentCore Identity (OAuth2 authorization code / 3-legged flow) and
GitHub's official remote MCP server (https://api.githubcopilot.com/mcp/).

Tokens live in the AgentCore token vault keyed by (workload identity, user ID). The workload
access token we pass in already encodes the user, so every Slack user gets their own GitHub
token. That per-user token is used as the Bearer credential against GitHub's remote MCP server,
which exposes GitHub's full tool catalog (profile, issues, pull requests, repositories, code
search, ...) scoped to whatever the user consented to -- instead of us hand-rolling individual
REST calls.
"""

import logging
from functools import lru_cache
from typing import Callable

import boto3
from strands import Agent, tool
from strands.models.bedrock import BedrockModel
from strands.tools.mcp import MCPClient
from strands.types.exceptions import MaxTokensReachedException, MCPClientInitializationError

import config
from auth_state import AuthState

logger = logging.getLogger(__name__)

MCP_SERVER_URL = "https://api.githubcopilot.com/mcp/"
SCOPES = ["repo", "read:user", "read:org"]
# The outer chat model is tuned for short Slack replies (max_tokens=1024), which is too tight
# once this agent has to chain tool calls (e.g. get_me -> search_repositories) through GitHub's
# verbose MCP tool schemas and then summarize the result -- it was hitting
# MaxTokensReachedException mid-answer. This nested agent gets its own, more generous budget.
GITHUB_AGENT_MAX_TOKENS = 4096
AUTH_REQUIRED_MESSAGE = (
    "AUTHORIZATION_REQUIRED: the user has not connected GitHub yet. A private connect link "
    "has been sent to them. Tell them to connect and then ask again. Do not guess GitHub data."
)
GITHUB_AGENT_SYSTEM_PROMPT = (
    "You are a GitHub assistant acting on behalf of the signed-in Slack user, using the GitHub "
    "tools available to you. Chain multiple tool calls when a request needs it -- most requests "
    "about 'my' repositories, issues, or pull requests need two calls: first get_me to find the "
    "user's login, then a search tool (e.g. search_repositories with query 'user:<login>', or "
    "search_issues/search_pull_requests with 'author:<login>') scoped to that login. For questions "
    "about which organizations the user belongs to or has access to, call get_teams with no "
    "'user' argument (it defaults to the authenticated user) and list the distinct organizations "
    "from the returned teams. Never answer that something can't be done without first trying the "
    "relevant tool(s) yourself. Answer briefly and only report what the tools return; never invent "
    "repositories, issues, pull requests, organizations, or profile data."
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


@lru_cache(maxsize=1)
def _github_agent_model() -> BedrockModel:
    return BedrockModel(
        model_id=config.GITHUB_MODEL_ID, region_name=config.AWS_REGION, temperature=0.2, max_tokens=GITHUB_AGENT_MAX_TOKENS
    )


def _run_github_mcp_agent(access_token: str, request: str) -> str:
    mcp_client = MCPClient(url=MCP_SERVER_URL, headers={"Authorization": f"Bearer {access_token}"})
    with mcp_client:
        github_agent = Agent(
            model=_github_agent_model(),
            system_prompt=GITHUB_AGENT_SYSTEM_PROMPT,
            tools=mcp_client.list_tools_sync(),
            callback_handler=None,
        )
        return str(github_agent(request)).strip()


def build_github_tool(get_workload_token: Callable[[], str], auth_state: AuthState):
    @tool
    def use_github(request: str) -> str:
        """Perform a GitHub action or answer a GitHub question for the Slack user who is asking,
        using GitHub's own tools (profile, issues, pull requests, repositories, code search, etc).

        Use this whenever the user asks about their GitHub account, repositories, issues, or pull requests.

        Args:
            request: A plain-language description of what to do on GitHub, e.g. "show my profile"
                or "list open issues assigned to me in acme/widgets".
        """
        try:
            workload_token = get_workload_token()
            token = fetch_token(workload_token)

            if token.get("accessToken"):
                try:
                    return _run_github_mcp_agent(token["accessToken"], request)
                except MCPClientInitializationError as err:
                    if "401" not in str(err):
                        raise
                    # Stored token was revoked or expired: ask the user to consent again.
                    token = fetch_token(workload_token, force=True)
                except MaxTokensReachedException:
                    logger.exception("GitHub tool hit the model's max_tokens limit")
                    return "ERROR: that GitHub request produced too much output; ask for something narrower"

            if token.get("authorizationUrl"):
                auth_state.provider = "GitHub"
                auth_state.authorization_url = token["authorizationUrl"]
                auth_state.session_uri = token.get("sessionUri")
                return AUTH_REQUIRED_MESSAGE
        except Exception:
            logger.exception("GitHub tool failed")
            return "ERROR: could not reach GitHub right now"

        return "ERROR: could not obtain a GitHub access token"

    return use_github
