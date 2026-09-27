"""Downloads a file someone attached in Slack, by its file ID, with the bot token.

The payload only carries file IDs (from the thread the worker read). The download URL
comes from Slack's own files.info answer, never from the payload, and the bot token is
only ever sent to files.slack.com:

  * files.info must give an https URL on files.slack.com, or we refuse.
  * The Authorization header is "unredirected": if Slack redirects the download
    elsewhere, the token stays behind, and only other Slack hosts are followed.
  * Slack answers a download it won't allow (e.g. a missing files:read scope) with an
    HTML sign-in page and status 200, so an HTML answer for a non-HTML file is refused.

Files are read into memory for this invocation only and never written anywhere.
"""

import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit

import slack_api

logger = logging.getLogger(__name__)

DOWNLOAD_HOST = "files.slack.com"
# Hosts Slack may redirect a download to (without our token).
_REDIRECT_HOST_SUFFIXES = (".slack.com", ".slack-edge.com")
TIMEOUT_SECONDS = 20


class FileUnavailable(Exception):
    """str() is a short reason fit to show the user."""


@dataclass(frozen=True)
class SlackFile:
    id: str
    name: str
    mimetype: str
    size: int
    url: str


def info(file_id: str) -> SlackFile:
    """Slack's own metadata for a file, including where to download it."""
    if not slack_api.bot_token():
        raise FileUnavailable("I don't have access to Slack files here")
    try:
        response = slack_api.call("files.info", {"file": file_id})
    except Exception as err:
        logger.warning("files.info failed for %s", file_id, exc_info=True)
        raise FileUnavailable("Slack didn't let me read it") from err
    if not response.get("ok"):
        logger.warning("files.info refused %s: %s", file_id, response.get("error"))
        raise FileUnavailable(_REASONS.get(response.get("error"), "Slack didn't let me read it"))
    file = response.get("file") or {}
    url = file.get("url_private_download") or file.get("url_private") or ""
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != DOWNLOAD_HOST:
        # External files (Google Drive and the like) and Slack Connect copies land here.
        logger.warning("File %s has no Slack download URL (%s)", file_id, parts.hostname)
        raise FileUnavailable("it isn't stored in Slack, so I can't download it")
    return SlackFile(
        id=file_id,
        name=file.get("name") or file.get("title") or "file",
        mimetype=file.get("mimetype") or "",
        size=int(file.get("size") or 0),
        url=url,
    )


def download(file: SlackFile, max_bytes: int) -> bytes:
    request = urllib.request.Request(file.url)
    request.add_unredirected_header("Authorization", f"Bearer {slack_api.bot_token()}")
    opener = urllib.request.build_opener(_SlackOnlyRedirects())
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            content_type = (response.headers.get("Content-Type") or "").lower()
            if content_type.startswith("text/html") and "html" not in file.mimetype:
                logger.warning("Slack sent a web page instead of file %s; is files:read granted?", file.id)
                raise FileUnavailable("Slack didn't let me download it")
            data = response.read(max_bytes + 1)
    except FileUnavailable:
        raise
    except Exception as err:
        logger.warning("Downloading file %s failed", file.id, exc_info=True)
        raise FileUnavailable("the download from Slack failed") from err
    if len(data) > max_bytes:
        raise FileUnavailable(f"it's larger than the {max_bytes // 1_000_000} MB I can read")
    return data


class _SlackOnlyRedirects(urllib.request.HTTPRedirectHandler):
    max_redirections = 3

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parts = urlsplit(newurl)
        host = parts.hostname or ""
        if parts.scheme != "https" or not host.endswith(_REDIRECT_HOST_SUFFIXES):
            raise urllib.error.HTTPError(newurl, code, "redirect away from Slack refused", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_REASONS = {
    "file_not_found": "it has been deleted",
    "file_deleted": "it has been deleted",
    "missing_scope": "this app isn't allowed to read files yet (it needs the files:read scope)",
    "not_authed": "I'm not signed in to Slack here",
    "invalid_auth": "I'm not signed in to Slack here",
}
