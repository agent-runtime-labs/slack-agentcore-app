import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import slack_progress  # noqa: E402


def _tool_use_event(name: str) -> dict:
    return {"event": {"contentBlockStart": {"start": {"toolUse": {"name": name}}}}}


@pytest.fixture(autouse=True)
def clear_token_cache():
    slack_progress._bot_token.cache_clear()
    yield
    slack_progress._bot_token.cache_clear()


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-fake")
    monkeypatch.delenv("SLACK_SECRET_ARN", raising=False)


@pytest.fixture
def posted(monkeypatch):
    calls = []
    monkeypatch.setattr(slack_progress, "_post_form", lambda url, fields: calls.append((url, fields)))
    return calls


@pytest.mark.parametrize(
    "tool_name,expected",
    [
        ("use_github", "\U0001f419 Checking Github…"),
        ("get_my_linkedin_profile", "\U0001f4bc Checking Linkedin Profile…"),
        ("use_linear", "\U0001f4cb Checking Linear…"),
        ("use_notion", "\U0001f4dd Checking Notion…"),
        ("use_some_future_provider", "\U0001f50e Checking Some Future Provider…"),
    ],
)
def test_friendly_tool_text(tool_name, expected):
    assert slack_progress._friendly_tool_text(tool_name) == expected


def test_on_event_posts_for_a_tool_use_start(token, posted):
    reporter = slack_progress.ProgressReporter("C1", "100.1")
    reporter.on_event(**_tool_use_event("use_github"))

    assert len(posted) == 1
    url, fields = posted[0]
    assert url == slack_progress.SLACK_CHAT_UPDATE_URL
    assert fields == {"token": "xoxb-fake", "channel": "C1", "ts": "100.1", "text": "\U0001f419 Checking Github…"}


def test_on_event_posts_answer_text_for_the_first_text_token(token, posted):
    reporter = slack_progress.ProgressReporter("C1", "100.1")
    reporter.on_event(data="partial text", complete=False)
    assert [fields["text"] for _, fields in posted] == [slack_progress.ANSWER_TEXT]


def test_on_event_posts_answer_text_for_reasoning_text_too(token, posted):
    reporter = slack_progress.ProgressReporter("C1", "100.1")
    reporter.on_event(reasoningText="thinking...")
    assert [fields["text"] for _, fields in posted] == [slack_progress.ANSWER_TEXT]


def test_repeated_text_chunks_only_post_the_answer_text_once(token, posted):
    reporter = slack_progress.ProgressReporter("C1", "100.1")
    for chunk in ("Here", " is", " your", " summary"):
        reporter.on_event(data=chunk, complete=False)
    assert len(posted) == 1


def test_answer_text_can_post_again_after_another_tool_call(token, posted):
    reporter = slack_progress.ProgressReporter("C1", "100.1")
    reporter.on_event(data="drafting...", complete=False)
    reporter.on_event(**_tool_use_event("use_linear"))
    reporter.on_event(data="final answer", complete=False)

    assert [fields["text"] for _, fields in posted] == [
        slack_progress.ANSWER_TEXT,
        "\U0001f4cb Checking Linear…",
        slack_progress.ANSWER_TEXT,
    ]


def test_on_event_ignores_events_with_neither_tool_nor_text(token, posted):
    reporter = slack_progress.ProgressReporter("C1", "100.1")
    reporter.on_event(complete=True)
    assert posted == []


def test_no_post_without_channel_or_ts(token, posted):
    reporter = slack_progress.ProgressReporter(None, None)
    reporter.on_event(**_tool_use_event("use_github"))
    assert posted == []


def test_no_post_without_a_bot_token(posted, monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_SECRET_ARN", raising=False)

    reporter = slack_progress.ProgressReporter("C1", "100.1")
    reporter.on_event(**_tool_use_event("use_github"))
    assert posted == []


def test_distinct_tools_in_quick_succession_all_post(token, posted):
    # The model can call several tools in one turn well under a second apart -- every
    # distinct tool must still get its own update (this was previously dropped by a
    # time-based throttle).
    reporter = slack_progress.ProgressReporter("C1", "100.1")
    reporter.on_event(**_tool_use_event("use_github"))
    reporter.on_event(**_tool_use_event("get_my_linkedin_profile"))
    reporter.on_event(**_tool_use_event("use_linear"))

    assert [fields["text"] for _, fields in posted] == [
        "\U0001f419 Checking Github…",
        "\U0001f4bc Checking Linkedin Profile…",
        "\U0001f4cb Checking Linear…",
    ]


def test_repeating_the_same_tool_does_not_repost(token, posted):
    reporter = slack_progress.ProgressReporter("C1", "100.1")
    reporter.on_event(**_tool_use_event("use_github"))
    reporter.on_event(**_tool_use_event("use_github"))
    assert len(posted) == 1


def test_a_failed_post_is_swallowed(token, monkeypatch):
    def boom(url, fields):
        raise RuntimeError("network down")

    monkeypatch.setattr(slack_progress, "_post_form", boom)

    reporter = slack_progress.ProgressReporter("C1", "100.1")
    reporter.on_event(**_tool_use_event("use_github"))  # must not raise
