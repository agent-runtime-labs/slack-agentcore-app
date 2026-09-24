"""Slack assistant running on Amazon Bedrock AgentCore Runtime.

Request payload (sent by the agent_worker Lambda):
    {"prompt": "...", "userId": "slack-T..-U..", "sessionId": "<64 hex chars>",
     "channel": "C...", "messageTs": "..."}
`channel`/`messageTs` identify the Slack placeholder message; they're optional and only
used to post live per-tool progress (see slack_progress.py) -- their absence never fails
the request.
Response:
    {"message": "...", "authRequired": null | {"authorizationUrl": "...", "sessionUri": "...",
                                               "cimd": {...}}}

A second request shape, {"userId": "...", "mode": "connections"}, skips the LLM and
prompt entirely and instead returns live connect status for every provider:
    {"connections": {"providers": [{"key": "...", "displayName": "...", "connected": bool,
                                     "authorizationUrl": str | null, ...}]}}
Powers the Slack App Home tab -- see _check_connections below.

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
from cimd import build_cimd_tools, check_cimd_connections, enabled_providers
from conversations import ConversationCache
from github import build_github_tool
from github import check_connection as check_github_connection
from linkedin import build_linkedin_tool, workload_token_provider
from linkedin import check_connection as check_linkedin_connection
from slack_progress import ProgressReporter

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("slack_agent")

# Wording tuned for Nova Micro: stricter phrasing made it refuse to recall earlier turns.
BASE_SYSTEM_PROMPT = """You are a friendly, concise assistant in Slack. Answer any question briefly using Slack mrkdwn.
Remember what the user tells you during the conversation and use it when they ask later.
Only call get_my_linkedin_profile when the user explicitly asks about their LinkedIn profile, and only call
use_github when the user explicitly asks about their GitHub account, repositories, issues, or pull requests."""

FORMATTING_RULE = """Keep replies tidy, not a report. When you're combining results from more than one tool, write
one short flowing paragraph or a single flat bullet list -- never a bold heading per source, never nested/indented
sub-bullets, and no more than a couple of short lines per item. Only bold the specific values that matter (a name, a
count, a status), not whole headings. If the answer is short, a sentence or two is better than a list at all."""

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
    lines += [FORMATTING_RULE, CONSENT_RULE, WRITE_RELAY_RULE]
    return "\n".join(lines)


app = BedrockAgentCoreApp()
model = BedrockModel(model_id=config.MODEL_ID, region_name=config.AWS_REGION, temperature=0.2, max_tokens=1024)
conversations = ConversationCache(config.MAX_CACHED_CONVERSATIONS)
SYSTEM_PROMPT = _system_prompt()


def _check_connections(user_id: str, get_token) -> dict:
    """Live connect status for every provider, without spending a chat turn.

    Bypasses the LLM entirely -- each check reuses the same fetch_token/discover code
    the real tools call, so "connected" here means exactly what it would mean if the
    user asked the bot. Powers the Slack App Home tab.
    """
    providers = [
        {"key": "linkedin", "displayName": "LinkedIn", **check_linkedin_connection(get_token)},
        {"key": "github", "displayName": "GitHub", **check_github_connection(get_token)},
        *check_cimd_connections(user_id),
    ]
    return {"providers": providers}


@app.entrypoint
def invoke(payload: dict, context: RequestContext) -> dict:
    user_id = payload.get("userId")
    if not user_id:
        return {"error": "userId is required", "authRequired": None}

    # In AgentCore Runtime this token is minted for the runtimeUserId the caller
    # passed, so it identifies the Slack user without trusting the payload.
    # Read it here (request thread) rather than inside the tool.
    get_token = workload_token_provider(BedrockAgentCoreContext.get_workload_access_token(), user_id)

    if payload.get("mode") == "connections":
        return {"connections": _check_connections(user_id, get_token), "message": "", "authRequired": None}

    prompt = (payload.get("prompt") or "").strip()
    session_id = context.session_id or payload.get("sessionId")
    if not prompt or not session_id:
        return {"error": "prompt and sessionId are required", "authRequired": None}

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

    progress = ProgressReporter(payload.get("channel"), payload.get("messageTs"))

    agent = Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=tools,
        messages=history,
        callback_handler=progress.on_event,
    )

    logger.info("Invoking agent for session %s with %d prior messages", session_id[:8], len(history))
    result = agent(prompt)
    conversations.put(session_id, agent.messages)

    return {"message": str(result).strip(), "authRequired": auth_state.as_dict()}


if __name__ == "__main__":
    app.run(host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8080")))  # nosec B104 - container
