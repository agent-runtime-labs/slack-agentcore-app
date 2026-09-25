import pytest

from slack_app import triage


class FakeBedrock:
    def __init__(self, answer="YES", error=None):
        self.answer = answer
        self.error = error
        self.requests = []

    def converse(self, **kwargs):
        self.requests.append(kwargs)
        if self.error:
            raise self.error
        return {"output": {"message": {"content": [{"text": self.answer}]}}}


@pytest.fixture
def bedrock(monkeypatch):
    client = FakeBedrock()
    monkeypatch.setattr(triage, "_bedrock", lambda: client)
    return client


@pytest.mark.parametrize("answer, expected", [("YES", True), (" yes.", True), ("NO", False), ("Maybe", False)])
def test_answer_is_parsed(bedrock, answer, expected):
    bedrock.answer = answer
    assert triage.wants_reply("how do I connect Notion?", bot_in_thread=False) is expected


def test_failure_means_staying_quiet(bedrock):
    bedrock.error = RuntimeError("throttled")
    assert triage.wants_reply("how do I connect Notion?", bot_in_thread=False) is False


def test_prompt_shows_mentions_as_a_reader_would(bedrock):
    triage.wants_reply("<@UBOB> <!here> lunch?", bot_in_thread=True)

    request = bedrock.requests[0]
    prompt = request["messages"][0]["content"][0]["text"]
    assert "@someone @here lunch?" in prompt
    assert "<@UBOB>" not in prompt
    assert "thread the assistant has been answering in" in prompt
    assert request["modelId"] == triage.DEFAULT_MODEL_ID
    assert request["inferenceConfig"]["temperature"] == 0


def test_model_is_configurable(bedrock, monkeypatch):
    monkeypatch.setenv("TRIAGE_MODEL_ID", "us.amazon.nova-lite-v1:0")
    triage.wants_reply("hi", bot_in_thread=False)
    assert bedrock.requests[0]["modelId"] == "us.amazon.nova-lite-v1:0"


def test_prompt_names_the_assistant(bedrock, monkeypatch):
    monkeypatch.setenv("ASSISTANT_NAME", "Helper")
    triage.wants_reply("Philip do you have access to it", bot_in_thread=True)
    assert 'named "Helper"' in bedrock.requests[0]["system"][0]["text"]
