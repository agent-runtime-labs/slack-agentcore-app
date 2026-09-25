import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import main  # noqa: E402

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
    return FakeAgent


def _payload(**overrides):
    return {"prompt": "which one is the oldest?", "userId": "slack-T1-UBOB", "sessionId": "s" * 64, **overrides}


def test_reply_uses_the_thread_and_keeps_no_history():
    main.invoke(_payload(thread=THREAD, requester="Bob"), SESSION)
    main.invoke(_payload(prompt="and the newest?", requester="Bob"), SESSION)

    first, second = created
    assert "messages" not in first.kwargs
    assert first.kwargs["tools"] == ["linkedin-tool", "github-tool"]
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
