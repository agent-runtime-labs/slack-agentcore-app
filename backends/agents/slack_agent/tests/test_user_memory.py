import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import user_memory  # noqa: E402
from user_memory import Scope, SettingsRequest  # noqa: E402

ALICE = "slack-T1-UALICE"


class FakeMemory:
    """bedrock-agentcore's memory calls, over records kept per namespace."""

    def __init__(self):
        self.records: dict[str, list[dict]] = {}
        self.events: list[dict] = []
        self.searches: list[tuple[str, str]] = []
        self.broken = False

    def _records(self, namespace):
        if self.broken:
            raise RuntimeError("memory is down")
        return {"memoryRecordSummaries": self.records.get(namespace, [])}

    def list_memory_records(self, memoryId, namespace, maxResults):  # noqa: N803
        return self._records(namespace)

    def retrieve_memory_records(self, memoryId, namespace, searchCriteria):  # noqa: N803
        self.searches.append((namespace, searchCriteria["searchQuery"]))
        return self._records(namespace)

    def create_event(self, **kwargs):
        if self.broken:
            raise RuntimeError("memory is down")
        self.events.append(kwargs)


def record(text, score=None):
    return {"memoryRecordId": "mem-1", "content": {"text": text}, **({"score": score} if score is not None else {})}


@pytest.fixture
def memory(monkeypatch):
    fake = FakeMemory()
    monkeypatch.setattr(user_memory.config, "USER_MEMORY_ID", "mem-store-1")
    monkeypatch.setattr(user_memory, "_memory", lambda: fake)
    return fake


# --- what the worker allows ------------------------------------------------------------


def test_scope_comes_from_the_worker(memory):
    scope = Scope.from_payload({"granted": True, "learn": "CPRIV", "recall": ["public", "CPRIV"]}, ALICE)
    assert scope == Scope(ALICE, "CPRIV", ("public", "CPRIV"))
    assert scope.actor("CPRIV") == "slack-T1-UALICE-CPRIV"


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {"granted": False, "learn": "dm", "recall": ["dm"]},
        {"granted": "true", "learn": "dm", "recall": ["dm"]},  # only a real yes counts
        {"learn": "dm", "recall": ["dm"]},
        {"granted": True},  # agreed, but no memory in this channel (Slack Connect)
        {"granted": True, "learn": "dm", "recall": "dm"},
        {"granted": True, "learn": "dm/../public", "recall": ["dm"]},
        {"granted": True, "learn": "dm", "recall": ["dm", "everyone"]},
    ],
)
def test_no_memory_without_consent_or_with_a_malformed_scope(memory, payload):
    assert Scope.from_payload(payload, ALICE) is None


def test_no_memory_without_a_memory_store(monkeypatch):
    monkeypatch.setattr(user_memory.config, "USER_MEMORY_ID", "")
    assert Scope.from_payload({"granted": True, "learn": "dm", "recall": ["dm"]}, ALICE) is None


# --- recall ----------------------------------------------------------------------------


def test_recall_lists_preferences_and_searches_facts_in_the_allowed_tiers_only(memory):
    memory.records = {
        f"/users/{ALICE}-public/preferences/": [
            record(json.dumps({"context": "said so", "preference": "Prefers short answers in bullet points"}))
        ],
        f"/users/{ALICE}-public/facts/": [record("Owns the payments-api service", 0.8), record("Likes tea", 0.05)],
        f"/users/{ALICE}-dm/facts/": [record("Preparing for a staff-role interview", 0.9)],
    }
    notes = user_memory.recall(Scope(ALICE, "public", ("public",)), "open an issue for the  flaky test")

    assert notes == ["Prefers short answers in bullet points", "Owns the payments-api service"]
    assert memory.searches == [(f"/users/{ALICE}-public/facts/", "open an issue for the flaky test")]


def test_a_dm_recalls_every_tier_it_was_given_without_repeats(memory):
    memory.records = {
        f"/users/{ALICE}-dm/facts/": [record("Preparing for a staff-role interview", 0.9)],
        f"/users/{ALICE}-public/facts/": [record("Owns the payments-api service", 0.9)],
        f"/users/{ALICE}-public/preferences/": [record("Prefers Go examples")],
        f"/users/{ALICE}-dm/preferences/": [record("Prefers Go examples")],
    }
    notes = user_memory.recall(Scope(ALICE, "dm", ("dm", "public")), "what should I focus on?")
    assert notes == ["Prefers Go examples", "Preparing for a staff-role interview", "Owns the payments-api service"]


def test_a_message_that_is_only_files_still_gets_preferences(memory):
    memory.records = {f"/users/{ALICE}-dm/preferences/": [record("Prefers short answers")]}
    assert user_memory.recall(Scope(ALICE, "dm", ("dm",)), "") == ["Prefers short answers"]
    assert memory.searches == []


def test_recall_failing_means_an_answer_without_notes(memory):
    memory.broken = True
    assert user_memory.recall(Scope(ALICE, "dm", ("dm", "public")), "hello") == []


# --- remember --------------------------------------------------------------------------


def test_the_turn_is_saved_under_the_tier_it_was_said_in(memory):
    scope = Scope(ALICE, "CPRIV", ("public", "CPRIV"))
    user_memory.remember(scope, "s" * 64, " keep it short please ", "Sure.", set())

    (event,) = memory.events
    assert event["memoryId"] == "mem-store-1"
    assert event["actorId"] == "slack-T1-UALICE-CPRIV"
    assert event["sessionId"] == "s" * 64
    assert event["payload"] == [
        {"conversational": {"content": {"text": "keep it short please"}, "role": "USER"}},
        {"conversational": {"content": {"text": "Sure."}, "role": "ASSISTANT"}},
    ]


@pytest.mark.parametrize("tool_name", ["use_github", "fetch_url", "read_attachment", "search_past_threads"])
def test_an_answer_built_from_a_tool_is_not_kept(memory, tool_name):
    user_memory.remember(Scope(ALICE, "dm", ("dm",)), "s1", "list my open PRs", "#12 secret-repo: fix", {tool_name})
    saved = json.dumps(memory.events[0]["payload"])
    assert "secret-repo" not in saved
    assert user_memory.TOOL_STUB in saved
    assert "list my open PRs" in saved


def test_a_diagram_does_not_hide_the_answer(memory):
    user_memory.remember(Scope(ALICE, "dm", ("dm",)), "s1", "draw the flow", "Here it is.", {"draw_diagram"})
    assert memory.events[0]["payload"][1]["conversational"]["content"]["text"] == "Here it is."


def test_nothing_is_saved_without_text_or_a_tier_to_save_to(memory):
    user_memory.remember(Scope(ALICE, "dm", ("dm",)), "s1", "  ", "ok", set())
    user_memory.remember(Scope(ALICE, None, ("C1",)), "s1", "hello", "ok", set())
    assert memory.events == []


def test_saving_failing_does_not_fail_the_answer(memory):
    memory.broken = True
    user_memory.remember(Scope(ALICE, "dm", ("dm",)), "s1", "hello", "hi", set())


def test_tools_used_reads_the_agents_messages():
    messages = [
        {"role": "user", "content": [{"text": "hi"}]},
        {"role": "assistant", "content": [{"toolUse": {"name": "use_github", "toolUseId": "1", "input": {}}}]},
        {"role": "user", "content": [{"toolResult": {"toolUseId": "1", "content": []}}]},
    ]
    assert user_memory.tools_used(messages) == {"use_github"}
    assert user_memory.tools_used(None) == set()


def test_the_settings_tool_only_raises_a_flag():
    request = SettingsRequest()
    settings = user_memory.build_memory_settings_tool(request)
    assert not request.requested
    answer = settings()
    assert request.requested
    assert "buttons" in answer
