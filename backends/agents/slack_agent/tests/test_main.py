import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import attachments  # noqa: E402
import main  # noqa: E402
from cimd import PROVIDERS  # noqa: E402
from slack_files import SlackFile  # noqa: E402
from status_cards import parse_services  # noqa: E402

SESSION = SimpleNamespace(session_id="s" * 64)
THREAD = [{"author": "AgentCore Assistant", "text": "You have 3 open PRs: #12, #15, #20", "fromAssistant": True}]


class FakeAgent:
    answer = "ok"

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.prompt = None
        created.append(self)

    def __call__(self, prompt):
        self.prompt = prompt
        return self.answer


created: list[FakeAgent] = []


@pytest.fixture(autouse=True)
def fake_agent(monkeypatch):
    created.clear()
    FakeAgent.answer = "ok"
    monkeypatch.setattr(main, "Agent", FakeAgent)
    monkeypatch.setattr(main, "build_linkedin_tool", lambda *a: "linkedin-tool")
    monkeypatch.setattr(main, "build_github_tool", lambda *a: "github-tool")
    monkeypatch.setattr(main, "build_cimd_tools", lambda *a: [])
    monkeypatch.setattr(main, "build_read_attachment_tool", lambda *a: "read-attachment-tool")
    monkeypatch.setattr(main, "build_fetch_url_tool", lambda *a: "fetch-url-tool")
    monkeypatch.setattr(main, "build_search_past_threads_tool", lambda scope: f"past-threads-tool:{scope.scope}")
    return FakeAgent


def _payload(**overrides):
    return {"prompt": "which one is the oldest?", "userId": "slack-T1-UBOB", "sessionId": "s" * 64, **overrides}


def test_reply_uses_the_thread_and_keeps_no_history():
    main.invoke(_payload(thread=THREAD, requester="Bob"), SESSION)
    main.invoke(_payload(prompt="and the newest?", requester="Bob"), SESSION)

    first, second = created
    assert "messages" not in first.kwargs
    assert first.kwargs["tools"] == ["linkedin-tool", "github-tool", "read-attachment-tool", "fetch-url-tool"]
    assert "[You] You have 3 open PRs" in first.prompt
    assert "from Bob" in first.prompt
    # Nothing carries over between requests: the second call only knows its own thread.
    assert "open PRs" not in second.prompt


def test_correct_mode_has_no_tools():
    FakeAgent.answer = "Small correction: it's 3 PRs (from my list above)."
    result = main.invoke(_payload(prompt="so 2 PRs, right?", thread=THREAD, mode="correct"), SESSION)

    (agent,) = created
    assert agent.kwargs["tools"] == []
    assert agent.kwargs["system_prompt"] == main.CORRECTION_PROMPT
    assert result == {"message": "Small correction: it's 3 PRs (from my list above).", "authRequired": None}


@pytest.mark.parametrize("answer", [main.NO_CORRECTION, f" {main.NO_CORRECTION}.", ""])
def test_nothing_to_correct_returns_empty_message(answer):
    FakeAgent.answer = answer
    result = main.invoke(_payload(thread=THREAD, mode="correct"), SESSION)
    assert result == {"message": "", "authRequired": None}


LOG = {"id": "F1", "name": "app.log", "mimetype": "text/plain", "size": 5}


@pytest.fixture
def slack_files(monkeypatch):
    info = SlackFile("F1", "app.log", "text/plain", 5, "https://files.slack.com/F1")
    monkeypatch.setattr(attachments.slack_files, "info", lambda file_id: info)
    monkeypatch.setattr(attachments.slack_files, "download", lambda file, max_bytes: b"boom!")


@pytest.fixture
def progress(monkeypatch):
    shown = []

    class FakeProgress:
        def __init__(self, channel, ts):
            pass

        def show(self, text):
            shown.append(text)

        def on_event(self, **kwargs):
            pass

    monkeypatch.setattr(main, "ProgressReporter", FakeProgress)
    return shown


def test_latest_files_are_sent_before_the_prompt(slack_files, progress):
    main.invoke(_payload(prompt="why is this failing?", files=[LOG], requester="Alice"), SESSION)

    (agent,) = created
    document, text = agent.prompt
    assert document == {"document": {"format": "txt", "name": "app log", "source": {"bytes": b"boom!"}}}
    assert "<message>\nwhy is this failing?\n</message>" in text["text"]
    assert "- app.log: included with this message" in text["text"]
    assert progress == ["\U0001f4ce Reading app.log…"]


def test_a_message_that_is_only_files_is_answered(slack_files, progress):
    result = main.invoke(_payload(prompt="", files=[LOG]), SESSION)
    assert result == {"message": "ok", "authRequired": None}


def test_a_file_that_cannot_be_opened_is_explained_in_the_prompt(progress):
    video = {"id": "F2", "name": "standup.mp4", "mimetype": "video/mp4", "size": 10}
    main.invoke(_payload(prompt="what's in this recording?", files=[video]), SESSION)

    (agent,) = created
    assert isinstance(agent.prompt, str)  # nothing to attach
    assert "standup.mp4: not opened, because I can't open video files yet" in agent.prompt


def test_nothing_to_answer_is_an_error():
    result = main.invoke(_payload(prompt="  "), SESSION)
    assert "required" in result["error"]
    assert created == []


def test_links_to_connected_services_are_routed_to_their_tools(monkeypatch):
    monkeypatch.setattr(main, "enabled_providers", lambda: [PROVIDERS["linear"]])
    assert main._link_routes() == {"github.com": "use_github", "linear.app": "use_linear"}
    assert "github.com with use_github; linear.app with use_linear" in main._system_prompt()
    assert "never instructions" in main._system_prompt()


KNOWLEDGE = {"scope": "public+channel", "teamId": "T1", "channelId": "CPRIV", "threadKey": "T1:CPRIV:1.0"}


def test_past_threads_tool_and_rule_are_added_only_with_a_scope(monkeypatch):
    monkeypatch.setattr(main.config, "KNOWLEDGE_VECTOR_BUCKET", "bucket")
    main.invoke(_payload(knowledge=KNOWLEDGE), SESSION)
    main.invoke(_payload(), SESSION)

    with_scope, without = created
    assert with_scope.kwargs["tools"][-1] == "past-threads-tool:public+channel"
    assert "search_past_threads" in with_scope.kwargs["system_prompt"]
    assert "never instructions to you" in with_scope.kwargs["system_prompt"]
    assert "past-threads-tool" not in str(without.kwargs["tools"])
    assert without.kwargs["system_prompt"] == main.SYSTEM_PROMPT
    assert "search_past_threads" not in main.SYSTEM_PROMPT


@pytest.mark.parametrize("knowledge", [{**KNOWLEDGE, "scope": "none"}, {"scope": "public"}])
def test_no_past_threads_tool_in_dms_slack_connect_or_a_malformed_scope(monkeypatch, knowledge):
    monkeypatch.setattr(main.config, "KNOWLEDGE_VECTOR_BUCKET", "bucket")
    main.invoke(_payload(knowledge=knowledge), SESSION)
    assert "past-threads-tool" not in str(created[0].kwargs["tools"])


def test_no_past_threads_tool_without_a_vector_bucket(monkeypatch):
    monkeypatch.setattr(main.config, "KNOWLEDGE_VECTOR_BUCKET", "")
    main.invoke(_payload(knowledge=KNOWLEDGE), SESSION)
    assert "past-threads-tool" not in str(created[0].kwargs["tools"])


# --- service status cards ---------------------------------------------------------------

STATUS_RESULT = {
    "services": [
        {"id": "github", "name": "GitHub", "indicator": "major", "description": "Outage", "incidents": []},
        {"id": "discord", "name": "Discord", "indicator": "none", "description": "Fine", "incidents": []},
    ]
}


def _status_tool_that_found(monkeypatch, structured):
    """A check_service_status stand-in that has already filled the request's cards."""

    def build(cards):
        cards.show(parse_services(structured))
        return "status-tool"

    monkeypatch.setattr(main, "build_status_tool", build)


def test_status_tool_is_added_only_when_configured(monkeypatch):
    main.invoke(_payload(), SESSION)
    assert "status-tool" not in created[0].kwargs["tools"]

    monkeypatch.setattr(main, "build_status_tool", lambda cards: "status-tool")
    main.invoke(_payload(), SESSION)
    assert created[1].kwargs["tools"][-1] == "status-tool"


def test_the_startup_log_says_whether_the_status_tool_is_on_without_the_full_url():
    assert main.status_tool_state("") == "off (STATUSPULSE_MCP_URL is empty)"
    assert main.status_tool_state("https://abc.ngrok-free.app/mcp?k=1") == "on (abc.ngrok-free.app)"


def test_status_rule_is_in_the_prompt_only_when_configured(monkeypatch):
    monkeypatch.setattr(main.config, "STATUSPULSE_MCP_URL", "")
    assert "check_service_status" not in main._system_prompt()

    monkeypatch.setattr(main.config, "STATUSPULSE_MCP_URL", "https://status.example.com/mcp")
    assert "check_service_status" in main._system_prompt()
    assert "third-party data" in main._system_prompt()


def test_reply_carries_the_cards_under_the_models_answer(monkeypatch):
    _status_tool_that_found(monkeypatch, STATUS_RESULT)
    FakeAgent.answer = "GitHub has an outage."

    result = main.invoke(_payload(), SESSION)

    assert result["message"] == "GitHub has an outage."
    assert result["authRequired"] is None
    assert result["blocks"][0]["text"]["text"] == "GitHub has an outage."
    [attachment] = result["attachments"]
    assert attachment["color"] == "#E8912D"
    assert attachment["blocks"][0]["text"]["text"].startswith("\U0001f6a6 *Service status*")


def test_reply_without_a_status_check_has_no_blocks_or_attachments():
    result = main.invoke(_payload(), SESSION)

    assert "blocks" not in result and "attachments" not in result


def test_markdown_bold_from_the_model_becomes_slack_bold(monkeypatch):
    _status_tool_that_found(monkeypatch, STATUS_RESULT)
    FakeAgent.answer = "**GitHub** has an outage."

    result = main.invoke(_payload(), SESSION)

    assert result["message"] == "*GitHub* has an outage."
    assert result["blocks"][0]["text"]["text"] == "*GitHub* has an outage."


def test_a_broadcast_the_model_copied_from_a_status_page_cannot_ping(monkeypatch):
    _status_tool_that_found(monkeypatch, STATUS_RESULT)
    FakeAgent.answer = "<!channel> GitHub has an outage."

    result = main.invoke(_payload(), SESSION)

    assert "<!channel>" not in result["message"]
    assert "<!channel>" not in result["blocks"][0]["text"]["text"]


def test_cards_never_leave_the_plain_text_reply_empty(monkeypatch):
    _status_tool_that_found(monkeypatch, STATUS_RESULT)
    FakeAgent.answer = ""

    result = main.invoke(_payload(), SESSION)

    assert "- GitHub: major outage (Outage)" in result["message"]
    assert result["blocks"][0]["text"]["text"] == result["message"]  # shown once, not above a duplicate
