import importlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import attachments  # noqa: E402
import main  # noqa: E402
from cimd import PROVIDERS  # noqa: E402
from slack_files import SlackFile  # noqa: E402

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
    monkeypatch.setattr(main, "build_draw_diagram_tool", lambda channel, thread_ts: f"diagram-tool:{channel}:{thread_ts}")
    monkeypatch.setattr(main, "diagrams_enabled", lambda channel, thread_ts: False)
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


TELEMETRY_VARS = ("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "OTEL_SEMCONV_STABILITY_OPT_IN")


@pytest.fixture
def telemetry_env(monkeypatch):
    """Lets a test reload main under its own TELEMETRY_CAPTURE_CONTENT and puts the variables back."""
    for name in (*TELEMETRY_VARS, "TELEMETRY_CAPTURE_CONTENT"):
        monkeypatch.delenv(name, raising=False)
    # main.py writes these straight into os.environ: have monkeypatch restore them afterwards.
    for name in TELEMETRY_VARS:
        monkeypatch.setenv(name, "")
    yield monkeypatch
    monkeypatch.delenv("TELEMETRY_CAPTURE_CONTENT", raising=False)
    importlib.reload(main)


def test_telemetry_carries_no_slack_text_by_default(telemetry_env):
    from opentelemetry.instrumentation.botocore.extensions.bedrock_utils import genai_capture_message_content

    # Whatever the platform turns on before our code runs, importing main pins it off.
    telemetry_env.setenv("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "true")
    assert genai_capture_message_content() is True
    importlib.reload(main)

    assert genai_capture_message_content() is False
    # An empty allow-list: Strands redacts every prompt, answer and tool input/result.
    assert os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"] == "gen_ai_unredacted_attributes="


@pytest.mark.parametrize("value", ["", "false", "0", "yes", "1", " true"])
def test_anything_but_true_keeps_content_out_of_telemetry(telemetry_env, value):
    telemetry_env.setenv("TELEMETRY_CAPTURE_CONTENT", value)
    importlib.reload(main)
    assert os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"] == "gen_ai_unredacted_attributes="
    assert os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] == "false"


@pytest.mark.parametrize("value", ["true", "TRUE", "True"])
def test_dev_can_turn_content_capture_on(telemetry_env, value):
    from opentelemetry.instrumentation.botocore.extensions.bedrock_utils import genai_capture_message_content

    telemetry_env.setenv("TELEMETRY_CAPTURE_CONTENT", value)
    importlib.reload(main)

    assert genai_capture_message_content() is True
    allowed = os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"].removeprefix("gen_ai_unredacted_attributes=").split(";")
    assert sorted(allowed) == ["gen_ai.input.messages", "gen_ai.output.messages", "gen_ai.system_instructions"]


def test_traces_are_grouped_by_thread_and_findable_by_channel_and_thread():
    thread_session = "T1:C123:1731654620.123456"
    main.invoke(_payload(thread=THREAD, requester="Bob", traceSessionId=thread_session), SESSION)
    main.invoke(_payload(thread=THREAD, mode="correct", traceSessionId=thread_session), SESSION)

    reply, correction = created
    assert reply.kwargs["name"] == "slack_assistant"
    assert reply.kwargs["trace_attributes"] == {
        "session.id": thread_session,  # what the dashboard groups by: one per thread
        "agentcore.session_id": "s" * 64,  # the per-person runtime session, as in AgentCore's own logs
        "slack.channel_id": "C123",
        "slack.thread_ts": "1731654620.123456",
        "slack.mode": "reply",
        "slack.thread_messages": 1,
        "slack.files": 0,
        "knowledge.scope": "off",
        "diagrams.enabled": False,
    }
    assert correction.kwargs["name"] == "slack_correction"
    assert correction.kwargs["trace_attributes"]["slack.mode"] == "correct"
    assert correction.kwargs["trace_attributes"]["session.id"] == thread_session
    assert correction.kwargs["trace_attributes"]["slack.channel_id"] == "C123"


def test_traces_carry_no_user_ids_or_text():
    main.invoke(_payload(thread=THREAD, requester="Bob", traceSessionId="T1:C123:1.0"), SESSION)

    (reply,) = created
    flat = repr(reply.kwargs["trace_attributes"])
    for leaked in ("Bob", "UBOB", "slack-T1", "open PRs", "oldest"):
        assert leaked not in flat


@pytest.mark.parametrize(
    "value", [None, "", 123, "C123:1.0", "T1:C123:abc", "T1:c123:1.0", "T1:C123:1.0\nextra", "T1:C123:1.0:UBOB", "x" * 500]
)
def test_a_missing_or_odd_trace_session_falls_back_to_the_runtime_session(value):
    main.invoke(_payload(thread=THREAD, traceSessionId=value), SESSION)

    (reply,) = created
    attributes = reply.kwargs["trace_attributes"]
    assert attributes["session.id"] == "s" * 64
    assert "slack.channel_id" not in attributes
    assert "slack.thread_ts" not in attributes


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


def test_diagram_tool_only_with_a_renderer_and_a_thread(monkeypatch):
    main.invoke(_payload(channel="C1", threadTs="1.1"), SESSION)
    monkeypatch.setattr(main, "diagrams_enabled", lambda channel, thread_ts: bool(channel and thread_ts))
    main.invoke(_payload(channel="C1", threadTs="1.1"), SESSION)
    main.invoke(_payload(channel="C1"), SESSION)

    off, on, no_thread = created
    assert "diagram-tool" not in str(off.kwargs["tools"])
    assert "draw_diagram" not in off.kwargs["system_prompt"]
    assert on.kwargs["tools"][-1] == "diagram-tool:C1:1.1"
    assert "draw_diagram" in on.kwargs["system_prompt"]
    assert "diagram-tool" not in str(no_thread.kwargs["tools"])


MEMORY = {"granted": True, "learn": "public", "recall": ["public"]}


@pytest.fixture
def memory(monkeypatch):
    """Per-user memory switched on, with recall and remember recorded instead of calling AWS."""
    saved = []
    monkeypatch.setattr(main.config, "USER_MEMORY_ID", "mem-store-1")
    monkeypatch.setattr(main.user_memory, "recall", lambda scope, query: [f"Prefers bullets ({','.join(scope.recall)})"])
    monkeypatch.setattr(main.user_memory, "remember", lambda *args: saved.append(args))
    monkeypatch.setattr(main.user_memory, "build_memory_settings_tool", lambda request: "memory-settings-tool")
    return saved


def test_without_per_user_memory_nothing_is_read_saved_or_offered(memory, monkeypatch):
    monkeypatch.setattr(main.config, "USER_MEMORY_ID", "")
    result = main.invoke(_payload(memory=MEMORY), SESSION)
    main.invoke(_payload(), SESSION)  # switched on, but the worker sent nothing

    for agent in created:
        assert "memory-settings-tool" not in agent.kwargs["tools"]
        assert "<about_requester>" not in agent.prompt
        assert agent.kwargs["system_prompt"] == main.SYSTEM_PROMPT
    assert memory == []
    assert result == {"message": "ok", "authRequired": None}


def test_with_consent_notes_are_added_and_the_turn_is_saved(memory):
    main.invoke(_payload(prompt="why is my Lambda timing out?", memory=MEMORY, thread=THREAD), SESSION)

    (agent,) = created
    assert "- Prefers bullets (public)" in agent.prompt
    assert main.MEMORY_ON_RULE in agent.kwargs["system_prompt"]
    assert agent.kwargs["tools"][-1] == "memory-settings-tool"
    ((scope, session_id, said, answer, used),) = memory
    assert (scope.user_id, scope.learn) == ("slack-T1-UBOB", "public")
    assert session_id == "s" * 64
    # Only the requester's own message is saved: never the thread other people wrote.
    assert said == "why is my Lambda timing out?"
    assert answer == "ok"
    assert used == set()


def test_without_consent_nothing_is_read_or_saved_but_settings_are_on_offer(memory):
    main.invoke(_payload(memory={"granted": False}), SESSION)

    (agent,) = created
    assert "<about_requester>" not in agent.prompt
    assert main.MEMORY_OFF_RULE in agent.kwargs["system_prompt"]
    assert agent.kwargs["tools"][-1] == "memory-settings-tool"
    assert memory == []


def test_correct_mode_never_touches_memory(memory):
    main.invoke(_payload(thread=THREAD, mode="correct", memory=MEMORY), SESSION)
    assert created[0].kwargs["tools"] == []
    assert memory == []


def test_nothing_is_saved_when_the_answer_is_connect_your_account(memory, monkeypatch):
    def needs_consent(get_token, auth_state):
        auth_state.provider, auth_state.authorization_url = "GitHub", "https://gh/auth"
        return "github-tool"

    monkeypatch.setattr(main, "build_github_tool", needs_consent)
    result = main.invoke(_payload(memory=MEMORY), SESSION)
    assert result["authRequired"]["provider"] == "GitHub"
    assert memory == []


def test_asking_for_memory_settings_is_passed_on_to_the_worker(memory, monkeypatch):
    def flagging_tool(request):
        request.requested = True
        return "memory-settings-tool"

    monkeypatch.setattr(main.user_memory, "build_memory_settings_tool", flagging_tool)
    result = main.invoke(_payload(prompt="turn off memory", memory=MEMORY), SESSION)
    assert result == {"message": "ok", "authRequired": None, "memorySettings": True}
