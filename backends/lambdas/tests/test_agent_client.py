import io
import re
import time

from slack_app import agent_client


def test_every_agent_call_carries_a_sampled_trace_context(monkeypatch):
    calls = []

    class FakeRuntime:
        def invoke_agent_runtime(self, **kwargs):
            calls.append(kwargs)
            return {"response": io.BytesIO(b'{"message": "ok", "authRequired": null}')}

    monkeypatch.delenv("AGENT_LOCAL_URL", raising=False)
    monkeypatch.setenv("AGENT_RUNTIME_ARN", "arn:aws:bedrock-agentcore:us-east-1:111122223333:runtime/r-abcdefghij")
    monkeypatch.setattr(agent_client, "_agentcore", lambda: FakeRuntime())

    for _ in range(2):
        assert agent_client.invoke_agent("hi", "slack-T1-U1", "s" * 64, "C1", "1.0")["message"] == "ok"

    first, second = calls
    xray = re.fullmatch(r"Root=1-([0-9a-f]{8})-([0-9a-f]{24});Parent=([0-9a-f]{16});Sampled=1", first["traceId"])
    w3c = re.fullmatch(r"00-([0-9a-f]{32})-([0-9a-f]{16})-01", first["traceParent"])
    assert xray and w3c
    # Both headers describe the same trace and parent span, so the agent can't be told two stories.
    assert xray.group(1) + xray.group(2) == w3c.group(1)
    assert xray.group(3) == w3c.group(2)
    assert first["traceId"] != second["traceId"]


def test_trace_ids_start_with_the_time_as_x_ray_requires():
    trace_id, _ = agent_client.sampled_trace_context()
    epoch_hex = trace_id.split("-")[1]
    assert abs(int(epoch_hex, 16) - time.time()) < 5
