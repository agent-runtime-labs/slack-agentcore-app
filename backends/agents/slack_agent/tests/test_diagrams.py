import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import config  # noqa: E402
import diagrams  # noqa: E402
import slack_api  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"pixels"
SOURCE = "flowchart LR\n  A --> B"
UPLOAD_URL = "https://files.slack.com/upload/v1/abc"


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    slack_api.bot_token.cache_clear()
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-fake")
    monkeypatch.delenv("SLACK_SECRET_ARN", raising=False)
    monkeypatch.setattr(config, "DIAGRAM_RENDER_URL", "https://kroki.example/")
    yield
    slack_api.bot_token.cache_clear()


@pytest.fixture
def http(monkeypatch):
    """Records every request; answers the renderer with PNG and the upload with OK."""
    requests = []

    def urlopen(request, timeout=None):
        requests.append(request)
        return Response(PNG if "kroki" in request.full_url else b"OK")

    monkeypatch.setattr(diagrams.urllib.request, "urlopen", urlopen)
    return requests


@pytest.fixture
def slack(monkeypatch):
    calls = []
    answers = {"files.getUploadURLExternal": {"ok": True, "upload_url": UPLOAD_URL, "file_id": "F9"}}

    def post(method, fields):
        calls.append((method, fields))
        return answers.get(method, {"ok": True})

    monkeypatch.setattr(slack_api, "post", post)
    return calls, answers


def test_draws_and_posts_the_image_in_the_thread(http, slack):
    calls, _ = slack
    result = diagrams.build_draw_diagram_tool("C1", "1.1")(mermaid=SOURCE, title="Flow")

    render, upload = http
    assert render.full_url == "https://kroki.example/mermaid/png"
    assert render.data == SOURCE.encode()
    # kroki.io answers 403 to urllib's default "Python-urllib" user agent.
    assert not render.get_header("User-agent", "").startswith("Python-urllib")
    assert render.get_header("User-agent")
    assert upload.full_url == UPLOAD_URL
    assert upload.data == PNG
    # The bot token goes to slack.com only, never with the bytes.
    assert not any("authorization" in key.lower() for request in http for key in request.headers)
    assert calls == [
        ("files.getUploadURLExternal", {"filename": "diagram.png", "length": str(len(PNG))}),
        (
            "files.completeUploadExternal",
            {"files": json.dumps([{"id": "F9", "title": "Flow"}]), "channel_id": "C1", "thread_ts": "1.1"},
        ),
    ]
    assert "posted in the thread" in result


def test_transparent_render_is_put_on_white(monkeypatch):
    """Kroki's PNG is transparent: dark labels vanish on Slack's dark theme."""
    from PIL import Image

    source = io.BytesIO()
    image = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    image.putpixel((1, 1), (10, 20, 30, 255))
    image.save(source, "PNG")
    monkeypatch.setattr(diagrams.urllib.request, "urlopen", lambda request, timeout=None: Response(source.getvalue()))

    out = Image.open(io.BytesIO(diagrams.render(SOURCE)))

    assert out.mode == "RGB"
    assert out.getpixel((0, 0)) == (255, 255, 255)
    assert out.getpixel((1, 1)) == (10, 20, 30)


def test_invalid_mermaid_goes_back_to_the_model(monkeypatch, slack):
    calls, _ = slack

    def urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 400, "Bad Request", {}, io.BytesIO(b"Parse error on line 2"))

    monkeypatch.setattr(diagrams.urllib.request, "urlopen", urlopen)
    result = diagrams.build_draw_diagram_tool("C1", "1.1")(mermaid="flowchart ???", title="Flow")
    assert "Parse error on line 2" in result
    assert "call draw_diagram again" in result
    assert calls == []


def test_renderer_must_return_a_png(monkeypatch):
    monkeypatch.setattr(diagrams.urllib.request, "urlopen", lambda request, timeout=None: Response(b"<html>"))
    with pytest.raises(diagrams.DiagramError, match="didn't return an image"):
        diagrams.render(SOURCE)


@pytest.mark.parametrize(
    "url",
    ["http://files.slack.com/upload", "https://files.slack.com.evil.example/upload", "https://evil.example/", ""],
)
def test_upload_only_goes_to_slack(http, slack, url):
    _, answers = slack
    answers["files.getUploadURLExternal"] = {"ok": True, "upload_url": url, "file_id": "F9"}
    with pytest.raises(diagrams.DiagramError):
        diagrams.upload(PNG, "Flow", "C1", "1.1")
    assert http == []


def test_missing_scope_is_explained(http, slack):
    _, answers = slack
    answers["files.getUploadURLExternal"] = {"ok": False, "error": "missing_scope"}
    result = diagrams.build_draw_diagram_tool("C1", "1.1")(mermaid=SOURCE, title="Flow")
    assert "files:write" in result


def test_stops_after_a_few_attempts(http, slack):
    draw = diagrams.build_draw_diagram_tool("C1", "1.1")
    for _ in range(diagrams.MAX_DRAWINGS):
        draw(mermaid=SOURCE, title="Flow")
    before = len(http)
    assert "No more diagrams" in draw(mermaid=SOURCE, title="Flow")
    assert len(http) == before


def test_oversized_or_empty_source_is_refused(http):
    draw = diagrams.build_draw_diagram_tool("C1", "1.1")
    assert "empty" in draw(mermaid="  ", title="Flow")
    assert "too large" in draw(mermaid="x" * (diagrams.MAX_SOURCE_CHARS + 1), title="Flow")
    assert http == []


@pytest.mark.parametrize(
    ("url", "channel", "thread_ts", "token", "expected"),
    [
        ("https://kroki.example", "C1", "1.1", "xoxb-fake", True),
        ("", "C1", "1.1", "xoxb-fake", False),
        ("https://kroki.example", None, "1.1", "xoxb-fake", False),
        ("https://kroki.example", "C1", None, "xoxb-fake", False),
        ("https://kroki.example", "C1", "1.1", "", False),
    ],
)
def test_enabled_needs_a_renderer_a_thread_and_a_token(monkeypatch, url, channel, thread_ts, token, expected):
    slack_api.bot_token.cache_clear()
    monkeypatch.setenv("SLACK_BOT_TOKEN", token)
    monkeypatch.setattr(config, "DIAGRAM_RENDER_URL", url)
    assert diagrams.enabled(channel, thread_ts) is expected
