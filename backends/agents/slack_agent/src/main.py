"""Slack assistant running on Amazon Bedrock AgentCore Runtime.

Request payload (sent by the agent_worker Lambda):
    {"prompt": "...", "userId": "slack-T..-U..", "sessionId": "<64 hex chars>"}
Response:
    {"message": "...", "authRequired": null | {"authorizationUrl": "...", "sessionUri": "...",
                                               "cimd": {...}}}

Tools come from two OAuth worlds:
    * linkedin.py / github.py  -- AgentCore Identity holds the tokens (client secret in AWS).
    * cimd/                    -- we hold the tokens; the client_id is a URL (CIMD/SEP-991).
Both raise consent the same way, through AuthState, so the Slack side is identical.
"""

import logging
import os

from bedrock_agentcore.runtime import BedrockAgentCoreApp, BedrockAgentCoreContext, RequestContext
from strands import Agent
from strands.models.bedrock import BedrockModel

import config
from auth_state import AuthState
from cimd import build_cimd_tools, enabled_providers
from conversations import ConversationCache
from github import build_github_tool
from linkedin import build_linkedin_tool, workload_token_provider

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("slack_agent")

# Wording tuned for Nova Micro: stricter phrasing made it refuse to recall earlier turns.
BASE_SYSTEM_PROMPT = """You are a friendly, concise assistant in Slack. Answer any question briefly using Slack mrkdwn.
Remember what the user tells you during the conversation and use it when they ask later.
Only call get_my_linkedin_profile when the user explicitly asks about their LinkedIn profile, and only call
use_github when the user explicitly asks about their GitHub account, repositories, issues, or pull requests."""

CONSENT_RULE = """If a tool returns AUTHORIZATION_REQUIRED, ask them to use the private Connect link that was just
sent to them and ask again. Never make up details for an account that is not connected."""

WRITE_RELAY_RULE = """When one of the use_* tools drafts a write action (opening an issue/PR, pushing files, editing a
page, etc.) and asks for confirmation, relay that draft to the user verbatim. If they confirm, call the same tool again
and restate the complete drafted details in the request along with the confirmation -- these tools have no memory of
the earlier draft, only what you pass them."""


def _system_prompt() -> str:
    """The base prompt plus one routing line per enabled CIMD provider."""
    lines = [BASE_SYSTEM_PROMPT]
    for provider in enabled_providers():
        lines.append(
            f"Only call {provider.tool_name} when the user explicitly asks about their "
            f"{provider.display_name} account or data ({provider.summary})."
        )
    lines += [CONSENT_RULE, WRITE_RELAY_RULE]
    return "\n".join(lines)


app = BedrockAgentCoreApp()
model = BedrockModel(model_id=config.MODEL_ID, region_name=config.AWS_REGION, temperature=0.2, max_tokens=1024)
conversations = ConversationCache(config.MAX_CACHED_CONVERSATIONS)
SYSTEM_PROMPT = _system_prompt()


@app.entrypoint
def invoke(payload: dict, context: RequestContext) -> dict:
    prompt = (payload.get("prompt") or "").strip()
    user_id = payload.get("userId")
    session_id = context.session_id or payload.get("sessionId")
    if not prompt or not user_id or not session_id:
        return {"error": "prompt, userId and sessionId are required", "authRequired": None}

    # In AgentCore Runtime this token is minted for the runtimeUserId the caller
    # passed, so it identifies the Slack user without trusting the payload.
    # Read it here (request thread) rather than inside the tool.
    get_token = workload_token_provider(BedrockAgentCoreContext.get_workload_access_token(), user_id)
    auth_state = AuthState()
    history = conversations.get(session_id)

    # CIMD tools key their token lookups on `userId` instead of the workload token,
    # because we own that vault rather than AgentCore. The runtime is IAM-only and the
    # worker Lambda derives `userId` from the Slack-signed event, so the value is as
    # trustworthy as the caller's role -- see docs/cimd-providers.md ("Trust model").
    tools = [
        build_linkedin_tool(get_token, auth_state),
        build_github_tool(get_token, auth_state),
        *build_cimd_tools(user_id, auth_state),
    ]

    agent = Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=tools,
        messages=history,
        callback_handler=None,
    )

    logger.info("Invoking agent for session %s with %d prior messages", session_id[:8], len(history))
    result = agent(prompt)
    conversations.put(session_id, agent.messages)

    return {"message": str(result).strip(), "authRequired": auth_state.as_dict()}


if __name__ == "__main__":
    app.run(host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8080")))  # nosec B104 - container
