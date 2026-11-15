"""Per-user memory on the Slack side: consent, where memory may be used, and the worker's part."""

import pytest
from test_knowledge_scope import PUBLIC, FakeSlack

from slack_app.handlers import agent_worker
from slack_app.user_memory import consent as consent_module
from slack_app.user_memory import forget as forget_module
from slack_app.user_memory import prompt
from slack_app.user_memory.consent import ASKED, DECLINED, GRANTED, consent_store
from slack_app.user_memory.scope import MAX_RECALL_TIERS, memory_scope

ALICE = "slack-T999-UALICE"
PRIVATE = {**PUBLIC, "is_private": True}


# --- consent ---------------------------------------------------------------------------


def test_nobody_has_agreed_until_they_say_so():
    store = consent_store()
    assert store.get(ALICE) is None

    assert store.mark_asked(ALICE) is True
    assert store.get(ALICE).status == ASKED
    assert not store.get(ALICE).granted
    assert store.mark_asked(ALICE) is False  # asked once, not on every message

    store.decide(ALICE, GRANTED)
    assert store.get(ALICE).granted
    store.decide(ALICE, DECLINED)
    assert not store.get(ALICE).granted


def test_a_yes_to_an_older_notice_no_longer_counts(monkeypatch):
    store = consent_store()
    store.decide(ALICE, GRANTED)
    monkeypatch.setattr(consent_module, "NOTICE_VERSION", consent_module.NOTICE_VERSION + 1)

    old = store.get(ALICE)
    assert not old.granted and not old.current
    assert store.mark_asked(ALICE) is True  # so they are asked again


def test_channel_tiers_are_kept_with_the_choice():
    store = consent_store()
    store.decide(ALICE, GRANTED)
    store.add_tier(ALICE, "CPRIV")
    store.decide(ALICE, DECLINED)
    assert store.get(ALICE).tiers == {"CPRIV"}  # still there for the deletion to find
    store.clear_tiers(ALICE)
    assert store.get(ALICE).tiers == frozenset()


# --- where memory may be used ----------------------------------------------------------


def test_a_dm_learns_privately_and_recalls_everything_the_person_has():
    slack = FakeSlack()
    scope = memory_scope(slack, "D123", frozenset({"CPRIV", "CGUEST"}))
    assert scope == {"learn": "dm", "recall": ["dm", "public", "CGUEST", "CPRIV"]}
    assert slack.calls == []


def test_a_dm_recalls_a_bounded_number_of_tiers():
    tiers = frozenset(f"C{n:03}" for n in range(20))
    assert len(memory_scope(FakeSlack(), "D123", tiers)["recall"]) == MAX_RECALL_TIERS


def test_a_public_channel_never_sees_what_was_said_in_a_dm_or_a_private_channel():
    assert memory_scope(FakeSlack(), "C1", frozenset({"CPRIV"})) == {"learn": "public", "recall": ["public"]}


def test_a_private_channel_keeps_its_own_memory_and_reads_public():
    assert memory_scope(FakeSlack(PRIVATE), "CPRIV") == {"learn": "CPRIV", "recall": ["public", "CPRIV"]}


def test_a_channel_with_a_guest_only_uses_its_own_memory():
    slack = FakeSlack(members=["UALICE", "UGUEST"], guests=["UGUEST"])
    assert memory_scope(slack, "C1") == {"learn": "C1", "recall": ["C1"]}


@pytest.mark.parametrize(
    "info",
    [
        {**PUBLIC, "is_ext_shared": True},  # Slack Connect
        {"is_mpim": True, "is_private": True, "is_ext_shared": False},  # group DM
        TimeoutError("slack is down"),
    ],
)
def test_no_memory_where_outsiders_may_be_or_slack_cannot_say(info):
    assert memory_scope(FakeSlack(info), "C1") is None


# --- the worker ------------------------------------------------------------------------

JOB = {
    "team_id": "T999",
    "channel": "D123",
    "user": "UALICE",
    "thread_ts": "1.1",
    "text": "keep it short please",
    "placeholder_ts": "1.2",
    "user_message_ts": "1.0",
}


class WorkerSlack(FakeSlack):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ephemeral = []

    def conversations_replies(self, **kwargs):
        return {"messages": []}

    def chat_update(self, **kwargs):
        pass

    def chat_postEphemeral(self, **kwargs):  # noqa: N802
        self.ephemeral.append(kwargs)

    def reactions_add(self, **kwargs):
        pass

    def reactions_remove(self, **kwargs):
        pass


@pytest.fixture
def worker(monkeypatch):
    """Runs a job with memory switched on; returns (slack, the payloads the agent was sent)."""
    monkeypatch.setenv("USER_MEMORY_ENABLED", "true")
    slack = WorkerSlack()
    sent = []
    answer = {"message": "ok", "authRequired": None}

    def fake_invoke(*args, **kwargs):
        sent.append(kwargs.get("memory"))
        return answer

    monkeypatch.setattr(agent_worker, "slack_client", lambda: slack)
    monkeypatch.setattr(agent_worker, "invoke_agent", fake_invoke)
    return slack, sent, answer


def test_memory_off_changes_nothing(monkeypatch, worker):
    slack, sent, _ = worker
    monkeypatch.delenv("USER_MEMORY_ENABLED")
    consent_store().decide(ALICE, GRANTED)

    agent_worker.process(JOB)
    assert sent == [None]
    assert slack.ephemeral == []


def test_someone_new_is_answered_without_memory_then_asked_once(worker):
    slack, sent, _ = worker
    agent_worker.process(JOB)
    agent_worker.process(JOB)

    assert sent == [{"granted": False}, {"granted": False}]
    (question,) = slack.ephemeral
    assert question["user"] == "UALICE" and question["thread_ts"] == "1.1"
    actions = [element["action_id"] for element in question["blocks"][1]["elements"]]
    assert actions == [prompt.GRANT, prompt.DECLINE]
    assert consent_store().get(ALICE).status == ASKED


def test_someone_who_said_no_is_not_asked_again(worker):
    slack, sent, _ = worker
    consent_store().decide(ALICE, DECLINED)
    agent_worker.process(JOB)
    assert sent == [{"granted": False}]
    assert slack.ephemeral == []


def test_someone_who_said_yes_gets_memory_for_this_channel(worker):
    slack, sent, _ = worker
    consent_store().decide(ALICE, GRANTED)
    agent_worker.process(JOB)
    assert sent == [{"granted": True, "learn": "dm", "recall": ["dm", "public"]}]
    assert slack.ephemeral == []


def test_a_channel_tier_is_noted_before_anything_is_saved_into_it(worker):
    slack, sent, _ = worker
    slack.info = PRIVATE
    consent_store().decide(ALICE, GRANTED)

    agent_worker.process({**JOB, "channel": "CPRIV"})
    assert sent == [{"granted": True, "learn": "CPRIV", "recall": ["public", "CPRIV"]}]
    assert consent_store().get(ALICE).tiers == {"CPRIV"}


def test_no_memory_if_the_tier_cannot_be_noted(monkeypatch, worker):
    slack, sent, _ = worker
    slack.info = PRIVATE
    consent_store().decide(ALICE, GRANTED)
    monkeypatch.setattr(consent_store(), "add_tier", lambda *a: (_ for _ in ()).throw(RuntimeError("dynamo down")))

    agent_worker.process({**JOB, "channel": "CPRIV"})
    assert sent == [None]  # answered, with no memory at all


def test_no_memory_and_no_question_if_consent_cannot_be_read(monkeypatch, worker):
    slack, sent, _ = worker
    monkeypatch.setattr(consent_store(), "get", lambda user_id: (_ for _ in ()).throw(RuntimeError("dynamo down")))
    agent_worker.process(JOB)
    assert sent == [None]
    assert slack.ephemeral == []


def test_asking_for_settings_shows_the_off_switch_to_someone_with_memory_on(worker):
    slack, _, answer = worker
    consent_store().decide(ALICE, GRANTED)
    answer["memorySettings"] = True

    agent_worker.process(JOB)
    (settings,) = slack.ephemeral
    actions = [element["action_id"] for element in settings["blocks"][1]["elements"]]
    assert actions == [prompt.FORGET, prompt.KEEP]
    assert consent_store().get(ALICE).granted  # showing the buttons changes nothing


def test_asking_for_settings_shows_the_question_again_after_a_no(worker):
    slack, _, answer = worker
    consent_store().decide(ALICE, DECLINED)
    answer["memorySettings"] = True

    agent_worker.process(JOB)
    actions = [element["action_id"] for element in slack.ephemeral[0]["blocks"][1]["elements"]]
    assert actions == [prompt.GRANT, prompt.DECLINE]
    assert consent_store().get(ALICE).status == DECLINED


def test_the_question_waits_until_the_account_is_connected(worker):
    slack, _, answer = worker
    answer["authRequired"] = {"provider": "GitHub", "authorizationUrl": "https://gh/auth"}
    agent_worker.process(JOB)
    assert [message["text"] for message in slack.ephemeral] == ["Connect your GitHub account: " + _link(slack)]
    assert consent_store().get(ALICE) is None


def _link(slack) -> str:
    return slack.ephemeral[0]["blocks"][0]["accessory"]["url"]


# --- forgetting ------------------------------------------------------------------------


class FakeMemory:
    """bedrock-agentcore's memory calls, over what's kept per actor and per namespace."""

    def __init__(self):
        self.events = {}  # actor -> {session: [event IDs]}
        self.records = {}  # namespace -> [record IDs]
        self.fail_records = False

    def list_sessions(self, memoryId, actorId, maxResults, nextToken=None):  # noqa: N803
        sessions = sorted(self.events.get(actorId, {}))
        start = int(nextToken or 0)
        page = {"sessionSummaries": [{"sessionId": session} for session in sessions[start : start + 1]]}
        return {**page, **({"nextToken": str(start + 1)} if start + 1 < len(sessions) else {})}

    def list_events(self, memoryId, actorId, sessionId, includePayloads, maxResults):  # noqa: N803
        return {"events": [{"eventId": event} for event in self.events[actorId][sessionId]]}

    def delete_event(self, memoryId, actorId, sessionId, eventId):  # noqa: N803
        self.events[actorId][sessionId].remove(eventId)

    def list_memory_records(self, memoryId, namespace, maxResults):  # noqa: N803
        return {"memoryRecordSummaries": [{"memoryRecordId": record} for record in self.records.get(namespace, [])]}

    def batch_delete_memory_records(self, memoryId, records):  # noqa: N803
        if self.fail_records:
            return {"failedRecords": records}
        gone = {record["memoryRecordId"] for record in records}
        for namespace, kept in self.records.items():
            self.records[namespace] = [record for record in kept if record not in gone]
        return {"successfulRecords": records, "failedRecords": []}


@pytest.fixture
def memory(monkeypatch):
    fake = FakeMemory()
    monkeypatch.setenv("USER_MEMORY_ID", "mem-1")
    monkeypatch.setattr(forget_module, "_memory", lambda: fake)
    fake.events = {
        f"{ALICE}-dm": {"s1": ["e1", "e2"], "s2": ["e3"]},
        f"{ALICE}-CPRIV": {"s3": ["e4"]},
        "slack-T999-UBOB-dm": {"s9": ["e9"]},
    }
    fake.records = {
        f"/users/{ALICE}-dm/preferences/": ["mem-1"],
        f"/users/{ALICE}-public/facts/": ["mem-2"],
        f"/users/{ALICE}-CPRIV/facts/": ["mem-3"],
        "/users/slack-T999-UBOB-dm/preferences/": ["mem-9"],
    }
    return fake


def test_turning_memory_off_deletes_every_tier_and_nobody_elses(memory):
    store = consent_store()
    store.decide(ALICE, GRANTED)
    store.add_tier(ALICE, "CPRIV")
    store.decide(ALICE, DECLINED)

    agent_worker.process({"kind": "forget_memory", "team_id": "T999", "user": "UALICE"})

    assert memory.events[f"{ALICE}-dm"] == {"s1": [], "s2": []}
    assert memory.events[f"{ALICE}-CPRIV"] == {"s3": []}
    assert memory.records[f"/users/{ALICE}-dm/preferences/"] == []
    assert memory.records[f"/users/{ALICE}-public/facts/"] == []
    assert memory.records[f"/users/{ALICE}-CPRIV/facts/"] == []
    assert store.get(ALICE).tiers == frozenset()
    # Bob's memory is untouched.
    assert memory.events["slack-T999-UBOB-dm"] == {"s9": ["e9"]}
    assert memory.records["/users/slack-T999-UBOB-dm/preferences/"] == ["mem-9"]


def test_a_failed_deletion_raises_so_the_queue_retries_it(memory):
    consent_store().decide(ALICE, DECLINED)
    memory.fail_records = True
    with pytest.raises(RuntimeError):
        agent_worker.process({"kind": "forget_memory", "team_id": "T999", "user": "UALICE"})
