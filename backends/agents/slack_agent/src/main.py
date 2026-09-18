"""Slack assistant running on Amazon Bedrock AgentCore Runtime.

Request payload (sent by the agent_worker Lambda):
    {"prompt": "...", "userId": "slack-T..-U..", "sessionId": "<64 hex chars>"}
Response:
    {"message": "...", "authRequired": null | {"authorizationUrl": "...", "sessionUri": "..."}}
"""

import logging
import os

from bedrock_agentcore.runtime import BedrockAgentCoreApp, BedrockAgentCoreContext, RequestContext
from strands import Agent
from strands.models.bedrock import BedrockModel

import config
from auth_state import AuthState
from conversations import ConversationCache
from github import build_github_tool
from linkedin import build_linkedin_tool, workload_token_provider

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("slack_agent")

# Wording tuned for Nova Micro: stricter phrasing made it refuse to recall earlier turns.
SYSTEM_PROMPT = """You are a friendly, concise assistant in Slack. Answer any question briefly using Slack mrkdwn.
Remember what the user tells you during the conversation and use it when they ask later.
Only call get_my_linkedin_profile when the user explicitly asks about their LinkedIn profile, and only call
use_github when the user explicitly asks about their GitHub account, repositories, issues, or pull requests.
If either returns AUTHORIZATION_REQUIRED, ask them to use the private Connect link that was just sent to them
and ask again. Never make up LinkedIn or GitHub details.
When use_github drafts a GitHub write action (opening an issue/PR, pushing files, etc.) and asks for
confirmation, relay that draft to the user verbatim. If they confirm, call use_github again and restate
the complete drafted details (repo, branch, title, body/content) in the request along with the
confirmation -- use_github has no memory of the earlier draft, only what you pass it."""

app = BedrockAgentCoreApp()
model = BedrockModel(model_id=config.MODEL_ID, region_name=config.AWS_REGION, temperature=0.2, max_tokens=1024)
conversations = ConversationCache(config.MAX_CACHED_CONVERSATIONS)


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

    agent = Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=[build_linkedin_tool(get_token, auth_state), build_github_tool(get_token, auth_state)],
        messages=history,
        callback_handler=None,
    )

    logger.info("Invoking agent for session %s with %d prior messages", session_id[:8], len(history))
    result = agent(prompt)
    conversations.put(session_id, agent.messages)

    return {"message": str(result).strip(), "authRequired": auth_state.as_dict()}


if __name__ == "__main__":
    app.run(host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8080")))  # nosec B104 - container
