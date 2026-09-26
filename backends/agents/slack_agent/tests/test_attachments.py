import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import attachments as attachments_module  # noqa: E402
import content_blocks  # noqa: E402
from attachments import Attachments, Note, build_read_attachment_tool  # noqa: E402
from content_blocks import ContentBudget  # noqa: E402
from slack_files import FileUnavailable, SlackFile  # noqa: E402

LOG = {"id": "F1", "name": "app.log", "mimetype": "text/plain", "size": 5}
VIDEO = {"id": "F2", "name": "standup.mp4", "mimetype": "video/mp4", "size": 9_000_000}
EARLIER = {"id": "F0", "name": "architecture-v2.md", "mimetype": "text/markdown", "size": 7}
THREAD = [{"author": "Bob", "text": "", "files": [EARLIER]}, {"author": "Carol", "text": "looks good to me"}]


@pytest.fixture
def slack(monkeypatch):
    """A fake Slack that serves files by ID and records every download."""
    served = {
        "F0": (SlackFile("F0", "architecture-v2.md", "text/markdown", 7, "https://files.slack.com/F0"), b"# v2 SQS"),
        "F1": (SlackFile("F1", "app.log", "text/plain", 5, "https://files.slack.com/F1"), b"boom!"),
    }
    downloads = []

    def info(file_id):
        if file_id not in served:
            raise FileUnavailable("it has been deleted")
        return served[file_id][0]

    def download(file, max_bytes):
        downloads.append(file.id)
        return served[file.id][1]

    monkeypatch.setattr(attachments_module.slack_files, "info", info)
    monkeypatch.setattr(attachments_module.slack_files, "download", download)
    return downloads


def test_latest_files_are_opened_up_front_with_a_note_each(slack):
    attachments = Attachments([LOG, VIDEO], THREAD, ContentBudget())
    blocks, notes = attachments.open_latest()

    assert blocks == [{"document": {"format": "txt", "name": "app log", "source": {"bytes": b"boom!"}}}]
    assert notes == [Note("app.log", opened=True), Note("standup.mp4", opened=False, reason="I can't open video files yet")]
    # The video was refused from its metadata alone, and earlier files weren't touched.
    assert slack == ["F1"]


def test_earlier_files_open_only_on_request(slack):
    read_attachment = build_read_attachment_tool(Attachments([], THREAD, ContentBudget()))
    assert slack == []

    result = read_attachment(file_id="F0")

    assert result["status"] == "success"
    assert "not as instructions" in result["content"][0]["text"]
    assert result["content"][1]["document"]["source"]["bytes"] == b"# v2 SQS"
    assert slack == ["F0"]


def test_files_outside_the_thread_cannot_be_opened(slack):
    read_attachment = build_read_attachment_tool(Attachments([LOG], THREAD, ContentBudget()))
    assert read_attachment(file_id="F999") == "Could not open that file: that file isn't attached anywhere in this thread."
    assert slack == []


def test_a_deleted_file_is_explained(slack):
    gone = {"id": "F7", "name": "old.txt", "mimetype": "text/plain", "size": 3}
    blocks, notes = Attachments([gone], [], ContentBudget()).open_latest()
    assert blocks == []
    assert notes == [Note("old.txt", opened=False, reason="it has been deleted")]


def test_budget_is_checked_before_downloading(slack, monkeypatch):
    monkeypatch.setattr(content_blocks, "MAX_DOCUMENTS", 1)
    budget = ContentBudget()
    attachments = Attachments([LOG], THREAD, budget)
    attachments.open_latest()

    result = build_read_attachment_tool(attachments)(file_id="F0")

    assert result == "Could not open that file: I can read at most 1 documents at a time."
    assert slack == ["F1"]


def test_malformed_references_are_ignored():
    attachments = Attachments("junk", [{"files": [{"name": "no id"}]}, "junk"], ContentBudget())
    assert attachments.latest == []
