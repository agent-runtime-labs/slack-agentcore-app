"""draw_diagram: turns Mermaid the model wrote into an image posted in the Slack thread.

Slack shows Mermaid source as plain text, so a diagram has to arrive as a picture:

  * The source is rendered to PNG by the diagram-renderer Lambda in our own account
    (DIAGRAM_RENDER_FUNCTION, see backends/diagram_renderer). The call is IAM-signed and
    the source never leaves AWS. The renderer's reason for refusing bad Mermaid goes back
    to the model, so it can fix the syntax.
  * The PNG is uploaded with Slack's external upload flow (files.getUploadURLExternal,
    a POST of the bytes, files.completeUploadExternal) and shared in the thread. That
    needs the files:write bot scope.

The bot token only ever goes to slack.com: the upload URL Slack hands back must be an
https URL on a Slack host, and the bytes are posted to it without the token.

The tool is off until DIAGRAM_RENDER_FUNCTION names a function.
"""

import base64
import json
import logging
import urllib.error
import urllib.request
from functools import lru_cache
from urllib.parse import urlsplit

import boto3
from botocore.config import Config
from strands import tool

import config
import slack_api

logger = logging.getLogger(__name__)

MAX_SOURCE_CHARS = 6_000
MAX_PNG_BYTES = 4_000_000  # the renderer refuses more: a synchronous Lambda response is 6 MB
MAX_RESPONSE_BYTES = 7_000_000
MAX_DRAWINGS = 3  # per request, failed attempts included, so a bad diagram can't loop
RENDER_TIMEOUT_SECONDS = 40  # includes a cold start of the renderer's Chromium
UPLOAD_TIMEOUT_SECONDS = 20
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_UPLOAD_HOST_SUFFIXES = (".slack.com",)
FILENAME = "diagram.png"


class DiagramError(Exception):
    """str() is a short reason fit to show the model."""


def enabled(channel: str | None, thread_ts: str | None) -> bool:
    """A renderer is configured, and we know which thread to post to and may post there."""
    return bool(config.DIAGRAM_RENDER_FUNCTION and channel and thread_ts and slack_api.bot_token())


@lru_cache(maxsize=1)
def _lambda_client():
    # No retries: a render that timed out has probably started a second Chromium already.
    return boto3.client(
        "lambda",
        config=Config(connect_timeout=5, read_timeout=RENDER_TIMEOUT_SECONDS, retries={"max_attempts": 0}),
    )


def render(source: str) -> bytes:
    """Mermaid source -> PNG bytes, by the renderer Lambda. Raises DiagramError with its reason."""
    try:
        response = _lambda_client().invoke(
            FunctionName=config.DIAGRAM_RENDER_FUNCTION, Payload=json.dumps({"mermaid": source}).encode("utf-8")
        )
        body = json.loads(response["Payload"].read(MAX_RESPONSE_BYTES))
    except Exception as err:
        logger.warning("Diagram renderer could not be reached", exc_info=True)
        raise DiagramError("the diagram renderer is unavailable right now") from err
    if response.get("FunctionError") or not isinstance(body, dict):
        # Only the error type: its message could quote the diagram.
        logger.warning("Diagram renderer failed: %s", body.get("errorType") if isinstance(body, dict) else "?")
        raise DiagramError("the diagram renderer is unavailable right now")
    reason = body.get("error")
    if reason == "invalid_source":
        raise DiagramError(f"the Mermaid source is invalid: {str(body.get('detail') or 'syntax error')[:600]}")
    if reason:
        raise DiagramError("the diagram is too large; draw a smaller one")
    try:
        png = base64.b64decode(body.get("png_base64") or "", validate=True)
    except ValueError as err:
        raise DiagramError("the diagram renderer didn't return an image") from err
    if len(png) > MAX_PNG_BYTES:
        raise DiagramError("the diagram is too large; draw a smaller one")
    if not png.startswith(_PNG_MAGIC):
        raise DiagramError("the diagram renderer didn't return an image")
    return png


def upload(png: bytes, title: str, channel: str, thread_ts: str) -> None:
    """Shares the PNG in the thread. Raises DiagramError."""
    ticket = _slack("files.getUploadURLExternal", {"filename": FILENAME, "length": str(len(png))})
    upload_url, file_id = ticket.get("upload_url") or "", ticket.get("file_id")
    parts = urlsplit(upload_url)
    if parts.scheme != "https" or not (parts.hostname or "").endswith(_UPLOAD_HOST_SUFFIXES) or not file_id:
        logger.warning("Slack gave an unexpected upload URL (%s)", parts.hostname)
        raise DiagramError("Slack didn't let me upload the image")
    request = urllib.request.Request(
        upload_url, data=png, headers={"Content-Type": "application/octet-stream"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=UPLOAD_TIMEOUT_SECONDS) as response:  # nosec B310 - checked above
            response.read()
    except Exception as err:
        logger.warning("Uploading the diagram to Slack failed", exc_info=True)
        raise DiagramError("the upload to Slack failed") from err
    _slack(
        "files.completeUploadExternal",
        {"files": json.dumps([{"id": file_id, "title": title}]), "channel_id": channel, "thread_ts": thread_ts},
    )


def _slack(method: str, fields: dict[str, str]) -> dict:
    try:
        response = slack_api.post(method, fields)
    except Exception as err:
        logger.warning("%s failed", method, exc_info=True)
        raise DiagramError("Slack didn't let me upload the image") from err
    if not response.get("ok"):
        logger.warning("%s refused: %s", method, response.get("error"))
        raise DiagramError(_REASONS.get(response.get("error"), "Slack didn't let me upload the image"))
    return response


def build_draw_diagram_tool(channel: str, thread_ts: str):
    attempts = 0

    @tool
    def draw_diagram(mermaid: str, title: str) -> str:
        """Draw a diagram or chart and post it in the thread as an image.

        Use it when a picture explains the answer better than text: a flow, a sequence of calls,
        an architecture, a timeline, a state machine, or numbers worth charting. Also use it when
        the user asks for a diagram or chart. Don't use it for short or simple answers.

        Args:
            mermaid: Valid Mermaid source (flowchart, sequenceDiagram, stateDiagram-v2, gantt,
                timeline, pie, xychart-beta, ...). Keep it small: about 15 nodes at most, short
                labels, and quote any label that contains punctuation.
            title: A short title for the image, e.g. "Checkout request flow".
        """
        nonlocal attempts
        attempts += 1
        if attempts > MAX_DRAWINGS:
            return "No more diagrams for this reply. Answer in text."
        source = (mermaid or "").strip()
        if not source:
            return "Could not draw that: the Mermaid source is empty."
        if len(source) > MAX_SOURCE_CHARS:
            return "Could not draw that: the diagram is too large; draw a smaller one."
        try:
            upload(render(source), (title or "Diagram").strip()[:80], channel, thread_ts)
        except DiagramError as reason:
            return f"Could not draw that: {reason}. Fix it and call draw_diagram again, or answer in text."
        return (
            "The diagram is posted in the thread as an image, below your reply. Don't repeat the "
            "Mermaid source: write one or two lines that say what it shows."
        )

    return draw_diagram


_REASONS = {
    "missing_scope": "this app isn't allowed to post files yet (it needs the files:write scope)",
    "not_in_channel": "I'm not a member of this channel, so I can't post files here",
    "not_authed": "I'm not signed in to Slack here",
    "invalid_auth": "I'm not signed in to Slack here",
}
