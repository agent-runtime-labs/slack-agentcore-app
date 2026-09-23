"""Turns a CimdProvider entry into a Strands tool the chat model can call.

The shape mirrors github.py: the outer chat model sees one coarse tool per provider
("use_linear"), and behind it a nested agent talks to the remote MCP server with the
signed-in user's own token. That keeps the provider's large tool catalogue out of the
chat model's context and gives the sub-agent a bigger token budget for chaining calls.

Token lifecycle handled here:

    stored token, still valid        -> use it
    stored token, expired            -> refresh, store, use it
    refresh rejected / 401 from MCP  -> forget the connection, ask for consent again
    no token                         -> ask for consent

"Ask for consent" means: build a PKCE authorization URL, hand it to AuthState, and let
main.py -> agent_worker -> Slack deliver it as the user's private connect link. That
path is shared with the AgentCore Identity tools, so nothing downstream changes.
"""

import logging

from strands import Agent, tool
from strands.models.bedrock import BedrockModel
from strands.tools.mcp import MCPClient
from strands.types.exceptions import MaxTokensReachedException, MCPClientInitializationError

import config
from auth_state import AuthState
from cimd import oauth
from cimd.discovery import AuthorizationServer, CimdUnsupported, discover
from cimd.providers import CimdProvider, enabled_providers
from cimd.tokens import DynamoTokenStore, StoredToken, token_store

logger = logging.getLogger(__name__)

# The outer chat model is tuned for short Slack replies (max_tokens=1024), which is too
# tight once a sub-agent has to chain tool calls through a remote server's verbose tool
# schemas and then summarise. Same reasoning as github.py.
SUB_AGENT_MAX_TOKENS = 4096

# Every write tool on every provider goes through the same confirm-then-act contract as
# GitHub's, because the sub-agent is stateless between turns: whatever the user agreed to
# has to be restated in the next request or it is treated as unconfirmed.
WRITE_CONFIRMATION_RULE = (
    "Before calling any tool that creates, updates, moves, archives or deletes something, you MUST "
    "first draft the exact action -- every field you intend to send -- and ask the user to confirm it. "
    "Do not call the write tool on that turn. Only proceed on a later turn where the incoming request "
    "explicitly says the user confirmed AND restates the complete concrete details to act on -- you have "
    "no memory of earlier turns, so if those details aren't in the request itself, treat it as "
    "unconfirmed and draft again rather than guessing what was agreed."
)


def _system_prompt(provider: CimdProvider) -> str:
    return (
        f"You are a {provider.display_name} assistant acting on behalf of the signed-in Slack user, "
        f"using the {provider.display_name} tools available to you. Chain multiple tool calls when a "
        "request needs it: when the user says 'my', first call the tool that identifies the current "
        "user (viewer / me / current user) and scope the follow-up call to that identity. Never answer "
        "that something cannot be done without first trying the relevant tool(s) yourself. Answer "
        "briefly, in Slack mrkdwn, and report only what the tools return -- never invent records, "
        "names, or ids. If a tool returns nothing, say the search found nothing rather than concluding "
        "the thing does not exist.\n\n"
        f"{WRITE_CONFIRMATION_RULE}\n\n{provider.guidance}"
    ).strip()


def _tool_description(provider: CimdProvider) -> str:
    also = f" Also covers questions phrased as {', '.join(provider.aliases)}." if provider.aliases else ""
    return (
        f"Do something in {provider.display_name} for the Slack user who is asking, using "
        f"{provider.display_name}'s own tools ({provider.summary}). Use this whenever the user "
        f"explicitly asks about their {provider.display_name} account or data.{also} "
        f"For actions that change {provider.display_name}, this tool always drafts the exact change "
        "and asks for confirmation first -- it never writes on the first call. When the user confirms, "
        "call this tool again and restate the complete drafted details in `request` along with the "
        "confirmation, because this tool has no memory of the earlier draft."
    )


def _auth_required_message(provider: CimdProvider) -> str:
    return (
        f"AUTHORIZATION_REQUIRED: the user has not connected {provider.display_name} yet. A private "
        "connect link has been sent to them. Tell them to connect and then ask again. Do not guess "
        f"{provider.display_name} data."
    )


def build_cimd_tools(user_id: str, auth_state: AuthState, store: DynamoTokenStore | None = None) -> list:
    """One tool per provider enabled for this deployment (empty list if none are).

    `user_id` is the AgentCore runtime user ID ("slack-<team>-<user>"), which is also the
    partition key of the token table -- so one Slack user can never reach another's tokens.
    """
    providers = enabled_providers()
    if not providers:
        return []

    store = store or token_store()
    logger.info("CIMD tools enabled: %s", ", ".join(p.key for p in providers))
    return [build_cimd_tool(provider, user_id, auth_state, store) for provider in providers]


def build_cimd_tool(provider: CimdProvider, user_id: str, auth_state: AuthState, store: DynamoTokenStore):
    def use_provider(request: str) -> str:
        """Run a request against this provider's MCP server.

        Args:
            request: A plain-language description of what to do, e.g. "list my open issues",
                or, when confirming a previously drafted change, the full restated details
                plus "the user confirmed this".
        """
        try:
            server = discover(provider)
            token = _usable_token(provider, server, user_id, store)

            if token:
                try:
                    return run_mcp_agent(provider, token.access_token, request)
                except MCPClientInitializationError as err:
                    if "401" not in str(err):
                        raise
                    # The server rejected a token we believed was valid: the user revoked
                    # access, or changed what the connection may see. Forget it and reconnect.
                    logger.info("%s rejected the stored token; asking the user to reconnect", provider.key)
                    store.delete(user_id, provider.key)
                except MaxTokensReachedException:
                    logger.exception("%s sub-agent hit the model's max_tokens limit", provider.key)
                    return f"ERROR: that {provider.display_name} request produced too much output; ask for something narrower"

            return _request_consent(provider, server, auth_state)
        except CimdUnsupported:
            logger.exception("%s cannot be used as a CIMD provider", provider.key)
            return f"ERROR: {provider.display_name} is not available right now"
        except Exception:
            logger.exception("%s tool failed", provider.key)
            return f"ERROR: could not reach {provider.display_name} right now"

    return tool(use_provider, name=provider.tool_name, description=_tool_description(provider))


def _usable_token(
    provider: CimdProvider, server: AuthorizationServer, user_id: str, store: DynamoTokenStore
) -> StoredToken | None:
    stored = store.get(user_id, provider.key)
    if not stored:
        return None
    if not stored.expired(oauth.EXPIRY_SKEW_SECONDS):
        return stored
    if not stored.refresh_token:
        return None

    try:
        refreshed = oauth.refresh(provider, server, stored)
    except Exception:
        # Refresh tokens are revoked when a user disconnects the app; that is an expected
        # end of life, not an error worth failing the turn over.
        logger.info("Refreshing the %s token failed; asking the user to reconnect", provider.key, exc_info=True)
        store.delete(user_id, provider.key)
        return None

    store.put(refreshed)
    return refreshed


def run_mcp_agent(provider: CimdProvider, access_token: str, request: str) -> str:
    """Open an MCP session as the user and let a nested agent work through it."""
    mcp_client = MCPClient(url=provider.mcp_url, headers={"Authorization": f"Bearer {access_token}"})
    with mcp_client:
        sub_agent = Agent(
            model=_sub_agent_model(),
            system_prompt=_system_prompt(provider),
            tools=mcp_client.list_tools_sync(),
            callback_handler=None,
        )
        return str(sub_agent(request)).strip()


def _request_consent(provider: CimdProvider, server: AuthorizationServer, auth_state: AuthState) -> str:
    consent = oauth.consent_request(provider, server)
    auth_state.provider = provider.display_name
    auth_state.authorization_url = consent.authorization_url
    auth_state.cimd = consent.as_payload()
    logger.info("Requesting %s consent (state %s...)", provider.key, consent.state[:6])
    return _auth_required_message(provider)


_MODEL: BedrockModel | None = None


def _sub_agent_model() -> BedrockModel:
    # Built once per container, not per tool: all providers share one model configuration.
    global _MODEL
    if _MODEL is None:
        _MODEL = BedrockModel(
            model_id=config.CIMD_MODEL_ID,
            region_name=config.AWS_REGION,
            temperature=0.2,
            max_tokens=SUB_AGENT_MAX_TOKENS,
        )
    return _MODEL
