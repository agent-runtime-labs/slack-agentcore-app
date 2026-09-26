import io
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import slack_api  # noqa: E402
import slack_files  # noqa: E402
from slack_files import FileUnavailable, SlackFile  # noqa: E402

FILE = {
    "id": "F1",
    "name": "q3-report.pdf",
    "mimetype": "application/pdf",
    "size": 1234,
    "url_private_download": "https://files.slack.com/files-pri/T1-F1/download/q3-report.pdf",
}


@pytest.fixture(autouse=True)
def token(monkeypatch):
    slack_api.bot_token.cache_clear()
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-fake")
    monkeypatch.delenv("SLACK_SECRET_ARN", raising=False)
    yield
    slack_api.bot_token.cache_clear()


def _files_info(monkeypatch, response):
    calls = []
    monkeypatch.setattr(slack_api, "call", lambda method, params: calls.append((method, params)) or response)
    return calls


def test_info_uses_slacks_own_metadata(monkeypatch):
    calls = _files_info(monkeypatch, {"ok": True, "file": FILE})
    file = slack_files.info("F1")
    assert calls == [("files.info", {"file": "F1"})]
    assert file == SlackFile("F1", "q3-report.pdf", "application/pdf", 1234, FILE["url_private_download"])


@pytest.mark.parametrize(
    "url",
    [
        "https://drive.google.com/file/d/abc",  # an external file
        "http://files.slack.com/files-pri/T1-F1/x.pdf",  # not https
        "https://files.slack.com.evil.example/x.pdf",
        "",
    ],
)
def test_info_refuses_anything_but_slacks_file_host(monkeypatch, url):
    _files_info(monkeypatch, {"ok": True, "file": {**FILE, "url_private_download": url, "url_private": url}})
    with pytest.raises(FileUnavailable, match="isn't stored in Slack"):
        slack_files.info("F1")


def test_info_explains_a_missing_scope(monkeypatch):
    _files_info(monkeypatch, {"ok": False, "error": "missing_scope"})
    with pytest.raises(FileUnavailable, match="files:read"):
        slack_files.info("F1")


def test_info_without_a_bot_token(monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN")
    slack_api.bot_token.cache_clear()
    with pytest.raises(FileUnavailable, match="access to Slack files"):
        slack_files.info("F1")


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, content_type: str):
        super().__init__(body)
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _serve(monkeypatch, body: bytes, content_type="application/pdf"):
    requests = []

    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            return FakeResponse(body, content_type)

    monkeypatch.setattr(slack_files.urllib.request, "build_opener", lambda *handlers: Opener())
    return requests


def _file(**overrides):
    return SlackFile(**{"id": "F1", "name": "a.pdf", "mimetype": "application/pdf", "size": 4, "url": FILE["url_private_download"], **overrides})


def test_download_sends_the_token_only_to_slack(monkeypatch):
    requests = _serve(monkeypatch, b"%PDF")
    assert slack_files.download(_file(), 100) == b"%PDF"
    (request,) = requests
    # Unredirected: urllib drops it if Slack redirects the download anywhere.
    assert request.unredirected_hdrs == {"Authorization": "Bearer xoxb-fake"}
    assert "Authorization" not in request.headers


def test_download_refuses_a_sign_in_page(monkeypatch):
    _serve(monkeypatch, b"<html>Sign in</html>", "text/html; charset=utf-8")
    with pytest.raises(FileUnavailable, match="didn't let me download"):
        slack_files.download(_file(), 100)


def test_download_refuses_oversized_files(monkeypatch):
    _serve(monkeypatch, b"x" * 101)
    with pytest.raises(FileUnavailable, match="larger than"):
        slack_files.download(_file(), 100)


def test_redirects_only_go_to_slack():
    handler = slack_files._SlackOnlyRedirects()
    request = slack_files.urllib.request.Request(FILE["url_private_download"])
    assert handler.redirect_request(request, None, 302, "Found", {}, "https://files-origin.slack.com/x").full_url
    with pytest.raises(urllib.error.HTTPError):
        handler.redirect_request(request, None, 302, "Found", {}, "https://evil.example/x")
    with pytest.raises(urllib.error.HTTPError):
        handler.redirect_request(request, None, 302, "Found", {}, "http://files.slack.com/x")
