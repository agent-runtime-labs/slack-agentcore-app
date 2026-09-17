"""Invoke the agent: AgentCore Runtime in AWS, or the local container under Tilt."""

import json
import os
import urllib.request
from functools import lru_cache

import boto3
from botocore.config import Config

SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"
TIMEOUT_SECONDS = 120


@lru_cache(maxsize=1)
def _agentcore():
    # No automatic retries: a retried invocation would run the agent twice.
    return boto3.client(
        "bedrock-agentcore",
        config=Config(read_timeout=TIMEOUT_SECONDS, retries={"total_max_attempts": 1}),
    )


def invoke_agent(prompt: str, runtime_user_id: str, session_id: str) -> dict:
    """Returns the agent's JSON response: {"message": str, "authRequired": dict | None}."""
    payload = json.dumps({"prompt": prompt, "userId": runtime_user_id, "sessionId": session_id})

    local_url = os.getenv("AGENT_LOCAL_URL")
    if local_url:
        return _invoke_local(local_url, payload, session_id)

    response = _agentcore().invoke_agent_runtime(
        agentRuntimeArn=os.environ["AGENT_RUNTIME_ARN"],
        qualifier="DEFAULT",
        runtimeSessionId=session_id,
        # Requires bedrock-agentcore:InvokeAgentRuntimeForUser. AgentCore mints a
        # workload access token bound to this user and hands it to the agent.
        runtimeUserId=runtime_user_id,
        contentType="application/json",
        accept="application/json",
        payload=payload.encode("utf-8"),
    )
    return json.loads(response["response"].read())


def _invoke_local(base_url: str, payload: str, session_id: str) -> dict:
    if not base_url.startswith(("http://", "https://")):
        raise ValueError("AGENT_LOCAL_URL must be an http(s) URL")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/invocations",
        data=payload.encode("utf-8"),
        headers={"Content-Type": "application/json", SESSION_HEADER: session_id},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # nosec B310 - scheme checked above
        return json.loads(response.read())
