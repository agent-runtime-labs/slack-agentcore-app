from slack_app import thread_history
from slack_app.attachments import Attachment
from slack_app.slack import PLACEHOLDER_TEXT, record_incoming, slack_client
from slack_app.thread_history import ThreadMessage, read_thread, readable


def _msg(ts, text, user=None, name=None, **extra):
    message = {"ts": ts, "text": text, **extra}
    if user:
        message["user"] = user
    if name:
        message["user_profile"] = {"display_name": name}
    return message


class FakeSlack:
    def __init__(self, pages):
        self.pages = list(pages)
        self.requests = []

    def conversations_replies(self, **kwargs):
        self.requests.append(kwargs)
        messages, cursor = self.pages.pop(0)
        return {"messages": messages, "response_metadata": {"next_cursor": cursor}}


THREAD = [
    _msg("1.0", "<@UBOT> what are my open PRs?", "UALICE", "Alice"),
    _msg("1.1", "You have 3 open PRs: #12, #15, #20", "UBOT"),
    _msg("1.2", "<@UBOB> can you review #15?", "UALICE", "Alice"),
    _msg("1.3", "", subtype="channel_join", user="UCAROL"),
    _msg("1.4", "Sure, is it the login fix one?", "UBOB", "Bob"),
]


def test_reads_the_thread_as_people_see_it():
    slack = FakeSlack([(THREAD, "")])
    thread = read_thread(slack, "C1", "1.0", "1.4", "UBOT")

    assert thread.history == [
        ThreadMessage("Alice", "@AgentCore Assistant what are my open PRs?"),
        ThreadMessage("AgentCore Assistant", "You have 3 open PRs: #12, #15, #20", from_assistant=True),
        ThreadMessage("Alice", "@Bob can you review #15?"),
    ]
    assert thread.message == ThreadMessage("Bob", "Sure, is it the login fix one?")
    assert thread.bot_in_thread
    assert slack.requests == [
        {"channel": "C1", "ts": "1.0", "latest": "1.4", "inclusive": True, "limit": thread_history.PAGE_SIZE}
    ]


def test_later_messages_and_placeholders_are_left_out():
    messages = [
        _msg("1.0", "first", "UALICE", "Alice"),
        _msg("1.1", PLACEHOLDER_TEXT, "UBOT"),
        _msg("1.2", "the new one", "UALICE", "Alice"),
        _msg("1.3", PLACEHOLDER_TEXT, "UBOT"),
    ]
    thread = read_thread(FakeSlack([(messages, "")]), "C1", "1.0", "1.2", "UBOT")

    assert thread.history == [ThreadMessage("Alice", "first")]
    assert thread.message.text == "the new one"
    assert not thread.bot_in_thread


def test_pages_are_followed():
    slack = FakeSlack([([_msg("1.0", "a", "UA", "A")], "next"), ([_msg("1.1", "b", "UA")], "")])
    thread = read_thread(slack, "C1", "1.0", "1.1", "UBOT")

    assert [message.text for message in thread.history] == ["a"]
    assert thread.message == ThreadMessage("A", "b")
    assert slack.requests[1]["cursor"] == "next"


def test_long_threads_keep_the_first_message_and_the_latest(monkeypatch):
    monkeypatch.setattr(thread_history, "MAX_MESSAGES", 3)
    messages = [_msg(f"1.{i}", f"m{i}", "UA", "A") for i in range(1, 8)]
    thread = read_thread(FakeSlack([(messages, "")]), "C1", "1.1", "1.7", "UBOT")

    assert [message.text for message in thread.history] == ["m1", "m5", "m6"]


def test_long_messages_are_truncated(monkeypatch):
    monkeypatch.setattr(thread_history, "MAX_CHARS", 5)
    messages = [_msg("1.0", "abcdefgh", "UA", "A"), _msg("1.1", "new", "UA")]
    thread = read_thread(FakeSlack([(messages, "")]), "C1", "1.0", "1.1", "UBOT")
    assert thread.history[0].text == "abcde…"


def test_other_bots_and_unknown_people():
    messages = [
        _msg("1.0", "deploy finished", bot_id="B1", subtype="bot_message", username="CI"),
        _msg("1.1", "nice", "UNONAME"),
        _msg("1.2", "new", "UA"),
    ]
    thread = read_thread(FakeSlack([(messages, "")]), "C1", "1.0", "1.2", "UBOT")
    assert thread.history == [ThreadMessage("CI", "deploy finished"), ThreadMessage("UNONAME", "nice")]


def test_failure_means_no_history():
    class Broken:
        def conversations_replies(self, **kwargs):
            raise RuntimeError("missing_scope")

    thread = read_thread(Broken(), "C1", "1.0", "1.1", "UBOT")
    assert thread.history == []
    assert thread.message is None


def test_readable():
    assert readable("<@UBOB> <!here> lunch?") == "@someone @here lunch?"
    assert readable("<@UBOB|bob> hi", {"UBOB": "Bob"}) == "@Bob hi"
    assert readable("<@UBOB|bob> hi") == "@bob hi"


def test_dry_run_client_serves_the_thread_it_has_seen():
    client = slack_client()
    record_incoming({"channel": "C1", "ts": "100.0", "user": "UALICE", "text": "hi bot"}, "100.0")
    posted = client.chat_postMessage(channel="C1", thread_ts="100.0", text=PLACEHOLDER_TEXT)
    client.chat_update(channel="C1", ts=posted["ts"], text="Hello!")
    record_incoming({"channel": "C1", "ts": "999999999999.0", "user": "UALICE", "text": "thanks"}, "100.0")

    thread = read_thread(client, "C1", "100.0", "999999999999.0", "UBOTLOCAL")

    assert thread.history == [
        ThreadMessage("UALICE", "hi bot"),
        ThreadMessage("AgentCore Assistant", "Hello!", from_assistant=True),
    ]
    assert thread.message == ThreadMessage("UALICE", "thanks")


def test_files_stay_in_the_thread_as_references():
    architecture = {"id": "F1", "name": "architecture-v2.pdf", "mimetype": "application/pdf", "size": 1_258_291}
    messages = [
        _msg("1.0", "", "UBOB", "Bob", subtype="file_share", files=[architecture]),
        _msg("1.1", "looks good to me", "UCAROL", "Carol"),
        _msg("1.2", "does v2 still use SQS FIFO?", "UALICE", "Alice"),
    ]
    thread = read_thread(FakeSlack([(messages, "")]), "C1", "1.0", "1.2", "UBOT")

    bob = thread.history[0]
    assert bob.text == ""
    assert bob.files == (Attachment("F1", "architecture-v2.pdf", "application/pdf", 1_258_291),)
    assert bob.with_files == "[attached: architecture-v2.pdf (1.2 MB)]"
    assert bob.as_dict() == {
        "author": "Bob",
        "text": "",
        "fromAssistant": False,
        "files": [{"id": "F1", "name": "architecture-v2.pdf", "mimetype": "application/pdf", "size": 1_258_291}],
    }
    assert thread.message.files == ()
    assert "files" not in thread.message.as_dict()


def test_dry_run_client_remembers_files():
    record_incoming({"channel": "C1", "ts": "100.0", "user": "UALICE", "text": "", "files": [{"id": "F9", "name": "a.png"}]}, "100.0")
    thread = read_thread(slack_client(), "C1", "100.0", "100.0", "UBOTLOCAL")
    assert thread.message.files == (Attachment("F9", "a.png"),)
