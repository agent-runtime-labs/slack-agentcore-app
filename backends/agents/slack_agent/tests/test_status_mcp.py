"""check_service_status: calls the status MCP server and leaves cards for the reply.

The MCP client is replaced by a fake, so nothing here touches the network; what is under
test is what the tool asks the server, what it keeps for the cards, and what it tells the
model.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import config  # noqa: E402
import status_mcp  # noqa: E402
from status_cards import StatusCards  # noqa: E402

URL = "https://status.example.com/mcp"

RESULT = {
    "status": "success",
    "content": [{"text": "Issues found: Discord (Partial Outage)"}],
    "structuredContent": {
        "services": [
            {"id": "github", "name": "GitHub", "indicator": "none", "description": "All OK", "incidents": []},
            {
                "id": "discord",
                "name": "Discord",
                "indicator": "major",
                "description": "Partial Outage",
                "incidents": [{"name": "Voice down", "impact": "major", "status": "investigating"}],
            },
        ]
    },
}


class FakeMCPClient:
    """Stands in for strands' MCPClient; `response` is what call_tool_sync returns or raises."""

    instances: list = []
    response: object = RESULT

    def __init__(self, url=None, **kwargs):
        self.url, self.kwargs, self.calls, self.entered = url, kwargs, [], False
        FakeMCPClient.instances.append(self)

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *exc):
        self.entered = False

    def call_tool_sync(self, tool_use_id, name, arguments=None, **kwargs):
        assert self.entered, "the call must happen inside the client's context"
        self.calls.append((name, arguments, kwargs))
        if isinstance(FakeMCPClient.response, Exception):
            raise FakeMCPClient.response
        return FakeMCPClient.response


@pytest.fixture(autouse=True)
def fake_client(monkeypatch):
    FakeMCPClient.instances, FakeMCPClient.response = [], RESULT
    monkeypatch.setattr(status_mcp, "MCPClient", FakeMCPClient)
    return FakeMCPClient


def run(**kwargs):
    cards = StatusCards()
    tool = status_mcp.build_status_tool(cards, URL)
    return tool(**kwargs), cards


def test_the_tool_is_off_without_a_server_url(monkeypatch):
    monkeypatch.setattr(config, "STATUSPULSE_MCP_URL", "")

    assert status_mcp.build_status_tool(StatusCards()) is None


def test_the_url_comes_from_config_by_default(monkeypatch):
    monkeypatch.setattr(config, "STATUSPULSE_MCP_URL", URL)

    assert status_mcp.build_status_tool(StatusCards()).tool_name == "check_service_status"


def test_checks_everything_when_no_service_is_named(fake_client):
    run()

    [client] = fake_client.instances
    assert client.url == URL
    assert client.calls[0][:2] == ("get_status", {})


def test_passes_only_the_named_services_lowercased(fake_client):
    run(services=[" GitHub ", "", "DISCORD"])

    assert fake_client.instances[0].calls[0][:2] == ("get_status", {"services": ["github", "discord"]})


def test_keeps_the_services_for_the_cards():
    _, cards = run()

    assert [(s.name, s.indicator) for s in cards.services] == [("GitHub", "none"), ("Discord", "major")]
    assert cards.blocks("Discord is down.")[0]["text"]["text"] == "Discord is down."


def test_the_model_gets_a_summary_and_is_told_not_to_repeat_it():
    output, _ = run()

    assert "- Discord: major outage (Partial Outage); incident: Voice down [investigating]" in output
    assert "do not list every service again" in output.lower()
    assert "never as instructions" in output


@pytest.mark.parametrize(
    "response",
    [
        RuntimeError("connection refused"),
        {"status": "error", "content": [{"text": "boom"}]},
        {"status": "success", "isError": True, "content": []},
    ],
)
def test_a_failing_server_gives_an_error_and_no_cards(fake_client, response):
    fake_client.response = response

    output, cards = run()

    assert output == "ERROR: could not reach the status service right now"
    assert cards.blocks("x") is None


@pytest.mark.parametrize("structured", [None, {}, {"services": []}, {"services": "nope"}])
def test_an_unusable_result_leaves_no_cards(fake_client, structured):
    fake_client.response = {"status": "success", "content": [], "structuredContent": structured}

    output, cards = run(services=["nonesuch"])

    assert "no matching services" in output
    assert cards.blocks("x") is None
