import pytest

from slack_app.handlers import agent_worker
from slack_app.knowledge import channels
from slack_app.knowledge.scope import search_scope

PUBLIC = {"is_channel": True, "is_private": False, "is_ext_shared": False, "is_member": True, "name": "platform"}


class FakeSlack:
    def __init__(self, info=None, members=("UALICE", "UBOB"), guests=()):
        self.info = PUBLIC if info is None else info
        self.members = list(members)
        self.guests = set(guests)
        self.calls: list[str] = []

    def conversations_info(self, channel):
        self.calls.append("conversations.info")
        if isinstance(self.info, Exception):
            raise self.info
        return {"channel": {"id": channel, **self.info}}

    def conversations_members(self, channel, limit, cursor=None):
        self.calls.append("conversations.members")
        start = int(cursor or 0)
        page = self.members[start : start + limit]
        more = start + limit < len(self.members)
        return {"members": page, "response_metadata": {"next_cursor": str(start + limit) if more else ""}}

    def users_info(self, user):
        self.calls.append("users.info")
        if user == "UBROKEN":
            raise RuntimeError("ratelimited")
        # Multi-channel guests are "restricted", single-channel guests "ultra restricted".
        multi = user.endswith("MULTI")
        guest = user in self.guests
        return {"user": {"id": user, "is_restricted": guest and multi, "is_ultra_restricted": guest and not multi}}


def scope_of(slack, channel="C1"):
    return search_scope(slack, "T1", channel, "1.0")["scope"]


def test_payload_names_the_team_channel_and_thread_to_leave_out():
    assert search_scope(FakeSlack(), "T1", "C1", "1726500000.000100") == {
        "scope": "public",
        "teamId": "T1",
        "channelId": "C1",
        "threadKey": "T1:C1:1726500000.000100",
    }


def test_a_public_channel_without_guests_finds_public_threads():
    assert scope_of(FakeSlack()) == "public"


def test_a_private_channel_also_finds_its_own_threads():
    assert scope_of(FakeSlack({**PUBLIC, "is_private": True})) == "public+channel"


@pytest.mark.parametrize("guest", ["UGUEST", "UGUESTMULTI"])  # single- and multi-channel guests
def test_a_channel_with_a_guest_only_finds_its_own_threads(guest):
    assert scope_of(FakeSlack(members=["UALICE", guest], guests=[guest])) == "channel"
    channels.clear_caches()
    assert scope_of(FakeSlack({**PUBLIC, "is_private": True}, members=[guest], guests=[guest])) == "channel"


def test_dms_get_no_tool_without_asking_slack():
    slack = FakeSlack()
    assert scope_of(slack, channel="D123") == "none"
    assert slack.calls == []


@pytest.mark.parametrize(
    "info",
    [
        {**PUBLIC, "is_ext_shared": True},  # Slack Connect
        {"is_mpim": True, "is_private": True, "is_ext_shared": False},  # group DM
        {"name": "missing flags"},
        TimeoutError("slack is down"),
    ],
)
def test_slack_connect_group_dms_and_unknown_channels_get_no_tool(info):
    assert scope_of(FakeSlack(info)) == "none"


def test_a_failed_guest_check_limits_search_to_the_channel():
    assert scope_of(FakeSlack(members=["UBROKEN"])) == "channel"


def test_a_very_large_channel_is_assumed_to_have_guests(monkeypatch):
    monkeypatch.setattr(channels, "MAX_MEMBERS_CHECKED", 3)
    assert scope_of(FakeSlack(members=[f"U{n}" for n in range(5)])) == "channel"


def test_the_guest_check_is_cached_per_channel_for_an_hour(monkeypatch):
    slack = FakeSlack(members=[f"U{n}" for n in range(450)])
    scope_of(slack)
    first = slack.calls.count("users.info")
    scope_of(slack)
    assert first == 450
    assert slack.calls.count("users.info") == first
    assert slack.calls.count("conversations.members") == 3  # 200 per page, once

    now = channels.time.time()
    monkeypatch.setattr(channels.time, "time", lambda: now + channels.GUEST_CACHE_SECONDS + 1)
    scope_of(slack)
    assert slack.calls.count("conversations.members") == 6


# --- agent_worker ----------------------------------------------------------------------

JOB = {
    "team_id": "T1",
    "channel": "C1",
    "user": "UALICE",
    "thread_ts": "1.1",
    "text": "has staging ECR throttling happened before?",
    "placeholder_ts": "1.2",
    "user_message_ts": "1.0",
}


class WorkerSlack(FakeSlack):
    def conversations_replies(self, **kwargs):
        return {"messages": []}

    def chat_update(self, **kwargs):
        pass

    def reactions_add(self, **kwargs):
        pass

    def reactions_remove(self, **kwargs):
        pass


@pytest.fixture
def invoked(monkeypatch):
    seen = []

    def fake_invoke(prompt, user_id, session_id, channel, message_ts, **kwargs):
        seen.append(kwargs)
        return {"message": "ok", "authRequired": None}

    monkeypatch.setattr(agent_worker, "invoke_agent", fake_invoke)
    return seen


def test_the_agent_gets_the_scope_when_knowledge_is_enabled(monkeypatch, invoked):
    monkeypatch.setenv("KNOWLEDGE_ENABLED", "true")
    monkeypatch.setattr(agent_worker, "slack_client", lambda: WorkerSlack({**PUBLIC, "is_private": True}))
    agent_worker.process(JOB)
    assert invoked[0]["knowledge"] == {"scope": "public+channel", "teamId": "T1", "channelId": "C1", "threadKey": "T1:C1:1.1"}


def test_with_knowledge_disabled_the_payload_is_unchanged(monkeypatch, invoked):
    slack = WorkerSlack()
    monkeypatch.setattr(agent_worker, "slack_client", lambda: slack)
    agent_worker.process(JOB)
    assert "knowledge" not in invoked[0]
    assert "conversations.info" not in slack.calls


def test_a_scope_that_cannot_be_worked_out_never_fails_the_answer(monkeypatch, invoked):
    monkeypatch.setenv("KNOWLEDGE_ENABLED", "true")
    monkeypatch.setattr(agent_worker, "slack_client", lambda: WorkerSlack())

    def broken(*args):
        raise RuntimeError("boom")

    monkeypatch.setattr(agent_worker, "search_scope", broken)
    agent_worker.process(JOB)
    assert "knowledge" not in invoked[0]
