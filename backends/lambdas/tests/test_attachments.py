from slack_app.attachments import MAX_FILES_PER_MESSAGE, Attachment, describe, from_files


def test_slack_files_become_references():
    files = [
        {"id": "F1", "name": "q3-report.pdf", "mimetype": "application/pdf", "size": 1_258_291, "url_private": "x"},
        {"id": "F2", "title": "Screenshot", "mimetype": "image/png", "size": "2048"},
    ]
    assert from_files(files) == (
        Attachment("F1", "q3-report.pdf", "application/pdf", 1_258_291),
        Attachment("F2", "Screenshot", "image/png", 2048),
    )
    # Only what the agent needs to ask Slack for the file: no URLs travel through the queue.
    assert from_files(files)[0].as_dict() == {
        "id": "F1",
        "name": "q3-report.pdf",
        "mimetype": "application/pdf",
        "size": 1_258_291,
    }


def test_deleted_hidden_and_malformed_files_are_skipped():
    files = [
        {"id": "F1", "name": "gone.pdf", "mode": "tombstone"},
        {"id": "F2", "name": "old.pdf", "mode": "hidden_by_limit"},
        {"name": "no-id.pdf"},
        "not a file",
        {"id": "F3", "name": "ok.txt", "size": None},
    ]
    assert from_files(files) == (Attachment("F3", "ok.txt"),)
    assert from_files(None) == ()


def test_number_of_files_is_capped():
    files = [{"id": f"F{i}", "name": f"{i}.png"} for i in range(MAX_FILES_PER_MESSAGE + 3)]
    assert len(from_files(files)) == MAX_FILES_PER_MESSAGE


def test_labels_read_like_slack():
    assert Attachment("F1", "q3-report.pdf", size=1_258_291).label == "q3-report.pdf (1.2 MB)"
    assert Attachment("F2", "notes.txt", size=12_400).label == "notes.txt (12 KB)"
    assert Attachment("F3", "tiny.txt", size=12).label == "tiny.txt (12 B)"
    assert Attachment("F4", "unknown.bin").label == "unknown.bin"
    assert describe((Attachment("F1", "a.png", size=2048), Attachment("F2", "b.pdf"))) == "[attached: a.png (2 KB), b.pdf]"
    assert describe(()) == ""
