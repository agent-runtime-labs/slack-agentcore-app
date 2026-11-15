"""Invoke the agent: AgentCore Runtime in AWS, or the local container under Tilt."""

import json
import os
import secrets
import time
import urllib.request
from functools import lru_cache

import boto3
from botocore.config import Config

SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"
TIMEOUT_SECONDS = 120

MODE_REPLY = "reply"
MODE_CORRECT = "correct"


@lru_cache(maxsize=1)
def _agentcore():
    # No automatic retries: a retried invocation would run the agent twice.
    return boto3.client(
        "bedrock-agentcore",
        config=Config(read_timeout=TIMEOUT_SECONDS, retries={"total_max_attempts": 1}),
    )


def sampled_trace_context() -> tuple[str, str]:
    """Trace headers for one agent call, as (X-Amzn-Trace-Id, W3C traceparent), marked sampled.

    Without them botocore copies the Lambda's own X-Amzn-Trace-Id onto the call. This function
    isn't traced, so that header says Sampled=0, the agent's OpenTelemetry follows it, and
    nothing is exported: no spans, and no sessions in GenAI Observability.
    """
    trace = f"{int(time.time()):08x}{secrets.token_hex(12)}"  # X-Ray's 8 hex of epoch seconds + 24 random
    span = secrets.token_hex(8)
    return f"Root=1-{trace[:8]}-{trace[8:]};Parent={span};Sampled=1", f"00-{trace}-{span}-01"


def invoke_agent(
    prompt: str,
    runtime_user_id: str,
    session_id: str,
    channel: str,
    message_ts: str | None,
    *,
    thread: list[dict] | None = None,
    requester: str | None = None,
    files: list[dict] | None = None,
    mode: str = MODE_REPLY,
    knowledge: dict | None = None,
    thread_ts: str | None = None,
) -> dict:
    """Returns the agent's JSON response: {"message": str, "authRequired": dict | None}.

    channel/message_ts identify the Slack placeholder message so the agent can post
    live per-tool progress to it directly (see slack_progress.py in the agent).
    thread is the conversation so far (ThreadMessage.as_dict()), the agent's only history.
    files are the new message's attachments (Attachment.as_dict()): references only, the
    agent downloads them from Slack itself.
    In MODE_CORRECT the agent gets no tools and returns an empty message if there is
    nothing to correct.
    knowledge is the search scope for past threads (knowledge/scope.py); without it the
    agent has no search_past_threads tool.
    thread_ts is the thread the placeholder sits in, where the agent posts the diagrams it
    draws (diagrams.py in the agent).
    """
    body = {
        "prompt": prompt,
        "userId": runtime_user_id,
        "sessionId": session_id,
        "channel": channel,
        "messageTs": message_ts,
        "threadTs": thread_ts,
        "thread": thread or [],
        "requester": requester,
        "files": files or [],
        "mode": mode,
    }
    if knowledge:
        body["knowledge"] = knowledge
    payload = json.dumps(body)

    local_url = os.getenv("AGENT_LOCAL_URL")
    if local_url:
        return _invoke_local(local_url, payload, session_id)

    trace_id, trace_parent = sampled_trace_context()
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
        traceId=trace_id,
        traceParent=trace_parent,
        # How OpenTelemetry learns the session: ADOT copies baggage onto spans, and GenAI
        # Observability groups traces into sessions by their session.id. It's a SHA-256 hex digest.
        baggage=f"session.id={session_id}",
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
