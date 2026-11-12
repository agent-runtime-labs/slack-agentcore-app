import base64
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import config  # noqa: E402
import diagrams  # noqa: E402
import slack_api  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"pixels"
SOURCE = "flowchart LR\n  A --> B"
UPLOAD_URL = "https://files.slack.com/upload/v1/abc"
FUNCTION = "slack-agentcore-dev-diagram-renderer"


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeLambda:
    """Stands in for boto3's Lambda client: records invocations, answers with `body`."""

    def __init__(self):
        self.invocations = []
        self.body = {"png_base64": base64.b64encode(PNG).decode()}
        self.function_error = None
        self.raises = None

    def invoke(self, FunctionName, Payload):
        self.invocations.append((FunctionName, json.loads(Payload)))
        if self.raises:
            raise self.raises
        reply = {"Payload": io.BytesIO(json.dumps(self.body).encode())}
        if self.function_error:
            reply["FunctionError"] = self.function_error
        return reply


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    slack_api.bot_token.cache_clear()
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-fake")
    monkeypatch.delenv("SLACK_SECRET_ARN", raising=False)
    monkeypatch.setattr(config, "DIAGRAM_RENDER_FUNCTION", FUNCTION)
    yield
    slack_api.bot_token.cache_clear()


@pytest.fixture
def renderer(monkeypatch):
    fake = FakeLambda()
    monkeypatch.setattr(diagrams, "_lambda_client", lambda: fake)
    return fake


@pytest.fixture
def http(monkeypatch):
    """Records every request that leaves for the network: only Slack's upload URL should."""
    requests = []

    def urlopen(request, timeout=None):
        requests.append(request)
        return Response(b"OK")

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


def test_draws_and_posts_the_image_in_the_thread(renderer, http, slack):
    calls, _ = slack
    result = diagrams.build_draw_diagram_tool("C1", "1.1")(mermaid=SOURCE, title="Flow")

    # The source goes to our own Lambda and nowhere else on the network.
    assert renderer.invocations == [(FUNCTION, {"mermaid": SOURCE})]
    (upload,) = http
    assert upload.full_url == UPLOAD_URL
    assert upload.data == PNG
    # The bot token goes to slack.com only, never with the bytes.
    assert not any("authorization" in key.lower() for key in upload.headers)
    assert calls == [
        ("files.getUploadURLExternal", {"filename": "diagram.png", "length": str(len(PNG))}),
        (
            "files.completeUploadExternal",
            {"files": json.dumps([{"id": "F9", "title": "Flow"}]), "channel_id": "C1", "thread_ts": "1.1"},
        ),
    ]
    assert "posted in the thread" in result


def test_invalid_mermaid_goes_back_to_the_model(renderer, http, slack):
    calls, _ = slack
    renderer.body = {"error": "invalid_source", "detail": "Parse error on line 2"}
    result = diagrams.build_draw_diagram_tool("C1", "1.1")(mermaid="flowchart ???", title="Flow")
    assert "Parse error on line 2" in result
    assert "call draw_diagram again" in result
    assert calls == [] and http == []


def test_renderer_that_is_down_is_reported_as_unavailable(renderer):
    renderer.raises = TimeoutError("read timed out")
    with pytest.raises(diagrams.DiagramError, match="unavailable"):
        diagrams.render(SOURCE)


def test_function_error_never_leaks_its_message(renderer):
    renderer.function_error = "Unhandled"
    renderer.body = {"errorType": "Error", "errorMessage": "Target closed: flowchart LR secret-node"}
    with pytest.raises(diagrams.DiagramError) as err:
        diagrams.render(SOURCE)
    assert "unavailable" in str(err.value)
    assert "secret-node" not in str(err.value)


@pytest.mark.parametrize("error", ["too_large", "empty"])
def test_refusals_from_the_renderer_are_explained(renderer, error):
    renderer.body = {"error": error, "detail": "x"}
    with pytest.raises(diagrams.DiagramError, match="too large"):
        diagrams.render(SOURCE)


@pytest.mark.parametrize("body", [{"png_base64": base64.b64encode(b"<html>").decode()}, {"png_base64": "!!!"}, {}])
def test_renderer_must_return_a_png(renderer, body):
    renderer.body = body
    with pytest.raises(diagrams.DiagramError, match="didn't return an image"):
        diagrams.render(SOURCE)


def test_oversized_png_is_refused(renderer):
    renderer.body = {"png_base64": base64.b64encode(PNG + b"x" * diagrams.MAX_PNG_BYTES).decode()}
    with pytest.raises(diagrams.DiagramError, match="too large"):
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


def test_missing_scope_is_explained(renderer, http, slack):
    _, answers = slack
    answers["files.getUploadURLExternal"] = {"ok": False, "error": "missing_scope"}
    result = diagrams.build_draw_diagram_tool("C1", "1.1")(mermaid=SOURCE, title="Flow")
    assert "files:write" in result


def test_stops_after_a_few_attempts(renderer, http, slack):
    draw = diagrams.build_draw_diagram_tool("C1", "1.1")
    for _ in range(diagrams.MAX_DRAWINGS):
        draw(mermaid=SOURCE, title="Flow")
    before = len(renderer.invocations)
    assert "No more diagrams" in draw(mermaid=SOURCE, title="Flow")
    assert len(renderer.invocations) == before


def test_oversized_or_empty_source_is_refused(renderer, http):
    draw = diagrams.build_draw_diagram_tool("C1", "1.1")
    assert "empty" in draw(mermaid="  ", title="Flow")
    assert "too large" in draw(mermaid="x" * (diagrams.MAX_SOURCE_CHARS + 1), title="Flow")
    assert renderer.invocations == [] and http == []


@pytest.mark.parametrize(
    ("function", "channel", "thread_ts", "token", "expected"),
    [
        (FUNCTION, "C1", "1.1", "xoxb-fake", True),
        ("", "C1", "1.1", "xoxb-fake", False),
        (FUNCTION, None, "1.1", "xoxb-fake", False),
        (FUNCTION, "C1", None, "xoxb-fake", False),
        (FUNCTION, "C1", "1.1", "", False),
    ],
)
def test_enabled_needs_a_renderer_a_thread_and_a_token(monkeypatch, function, channel, thread_ts, token, expected):
    slack_api.bot_token.cache_clear()
    monkeypatch.setenv("SLACK_BOT_TOKEN", token)
    monkeypatch.setattr(config, "DIAGRAM_RENDER_FUNCTION", function)
    assert diagrams.enabled(channel, thread_ts) is expected
