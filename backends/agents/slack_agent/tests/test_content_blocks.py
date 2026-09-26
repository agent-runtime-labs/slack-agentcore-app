import io
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import content_blocks  # noqa: E402
from content_blocks import DOCUMENT, IMAGE, ContentBudget, Unsupported, check_size, classify  # noqa: E402


def _png(width=10, height=10) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), "red").save(out, format="PNG")
    return out.getvalue()


@pytest.mark.parametrize(
    "name, mimetype, block, fmt",
    [
        ("error.png", "image/png", IMAGE, "png"),
        ("photo.JPG", "image/jpeg", IMAGE, "jpeg"),
        ("q3-report.pdf", "application/pdf", DOCUMENT, "pdf"),
        ("proposal.docx", "application/octet-stream", DOCUMENT, "docx"),
        ("customers.xlsx", "", DOCUMENT, "xlsx"),
        ("q3.csv", "text/csv", DOCUMENT, "csv"),
        ("README.md", "text/plain", DOCUMENT, "md"),
        ("notes", "text/plain; charset=utf-8", DOCUMENT, "txt"),
        ("config.json", "application/json", DOCUMENT, "txt"),
        ("main.py", "text/x-python", DOCUMENT, "txt"),
        ("app.log", "application/octet-stream", DOCUMENT, "txt"),
    ],
)
def test_classify(name, mimetype, block, fmt):
    kind = classify(name, mimetype)
    assert (kind.block, kind.format) == (block, fmt)


@pytest.mark.parametrize(
    "name, mimetype, reason",
    [
        ("standup.mp4", "video/mp4", "video files"),
        ("call.m4a", "audio/mp4", "audio files"),
        ("logo.svg", "image/svg+xml", "PNG, JPEG, GIF and WebP"),
        ("backup.zip", "application/zip", ".zip"),
    ],
)
def test_unsupported_files_say_why(name, mimetype, reason):
    with pytest.raises(Unsupported, match=reason):
        classify(name, mimetype)


def test_size_limits_depend_on_the_kind():
    check_size(classify("photo.jpg", "image/jpeg"), 15_000_000)  # scaled down later
    with pytest.raises(Unsupported, match="larger than the 4 MB"):
        check_size(classify("big.pdf", "application/pdf"), 5_000_000)


def test_small_image_is_sent_unchanged():
    data = _png()
    block = ContentBudget().block(classify("a.png", "image/png"), "a.png", data)
    assert block == {"image": {"format": "png", "source": {"bytes": data}}}


def test_large_image_is_scaled_down():
    block = ContentBudget().block(classify("big.png", "image/png"), "big.png", _png(4000, 3000))
    with Image.open(io.BytesIO(block["image"]["source"]["bytes"])) as image:
        assert max(image.size) == content_blocks.MAX_IMAGE_EDGE


def test_a_file_that_is_not_really_an_image_is_refused():
    with pytest.raises(Unsupported, match="valid image"):
        ContentBudget().block(classify("a.png", "image/png"), "a.png", b"<html>sign in</html>")


def test_documents_get_safe_unique_names():
    budget = ContentBudget()
    kind = classify("q3 report.v2.pdf", "application/pdf")
    first = budget.block(kind, "q3 report.v2.pdf", b"%PDF-1.4")
    second = budget.block(kind, "q3 report.v2.pdf", b"%PDF-1.4")
    assert first["document"]["name"] == "q3 report v2 pdf"
    assert second["document"]["name"] == "q3 report v2 pdf (2)"
    assert first["document"]["format"] == "pdf"


def test_text_documents_must_be_utf8():
    kind = classify("app.log", "text/plain")
    assert ContentBudget().block(kind, "app.log", "ok ✓".encode())["document"]["format"] == "txt"
    with pytest.raises(Unsupported, match="text file"):
        ContentBudget().block(kind, "app.log", b"\xff\xfe\x00binary")


def test_budget_enforces_converse_limits(monkeypatch):
    monkeypatch.setattr(content_blocks, "MAX_DOCUMENTS", 1)
    budget = ContentBudget()
    kind = classify("a.pdf", "application/pdf")
    budget.block(kind, "a.pdf", b"%PDF")
    with pytest.raises(Unsupported, match="at most 1 documents"):
        budget.check(kind)
    budget.check(classify("a.png", "image/png"))  # images have their own allowance
