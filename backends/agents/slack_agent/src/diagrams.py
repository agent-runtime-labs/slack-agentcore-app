"""draw_diagram: turns Mermaid the model wrote into an image posted in the Slack thread.

Slack shows Mermaid source as plain text, so a diagram has to arrive as a picture:

  * The source is rendered to PNG by a Kroki server (DIAGRAM_RENDER_URL). Kroki's
    reason for refusing bad Mermaid goes back to the model, so it can fix the syntax.
  * The PNG is uploaded with Slack's external upload flow (files.getUploadURLExternal,
    a POST of the bytes, files.completeUploadExternal) and shared in the thread. That
    needs the files:write bot scope.

The bot token only ever goes to slack.com: the upload URL Slack hands back must be an
https URL on a Slack host, and the bytes are posted to it without the token.

The diagram source leaves for whichever server DIAGRAM_RENDER_URL names, so the tool is
off until that is set: point it at a Kroki you run for anything that isn't public.
"""

import io
import json
import logging
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from PIL import Image
from strands import tool

import config
import slack_api

logger = logging.getLogger(__name__)

MAX_SOURCE_CHARS = 6_000
MAX_PNG_BYTES = 5_000_000
MAX_DRAWINGS = 3  # per request, failed attempts included, so a bad diagram can't loop
RENDER_TIMEOUT_SECONDS = 20
UPLOAD_TIMEOUT_SECONDS = 20
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_UPLOAD_HOST_SUFFIXES = (".slack.com",)
FILENAME = "diagram.png"
USER_AGENT = "slack-agentcore-assistant/1.0"


class DiagramError(Exception):
    """str() is a short reason fit to show the model."""


def enabled(channel: str | None, thread_ts: str | None) -> bool:
    """A renderer is configured, and we know which thread to post to and may post there."""
    return bool(config.DIAGRAM_RENDER_URL and channel and thread_ts and slack_api.bot_token())


def render(source: str) -> bytes:
    """Mermaid source -> PNG bytes, by Kroki. Raises DiagramError with Kroki's reason."""
    base = config.DIAGRAM_RENDER_URL.rstrip("/")
    if urlsplit(base).scheme not in ("http", "https"):
        raise DiagramError("the diagram renderer isn't configured correctly")
    request = urllib.request.Request(
        f"{base}/mermaid/png",
        data=source.encode("utf-8"),
        # kroki.io answers 403 to urllib's default "Python-urllib" user agent.
        headers={"Content-Type": "text/plain", "Accept": "image/png", "User-Agent": USER_AGENT},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=RENDER_TIMEOUT_SECONDS) as response:  # nosec B310 - scheme checked
            png = response.read(MAX_PNG_BYTES + 1)
    except urllib.error.HTTPError as err:
        detail = err.read(600).decode("utf-8", "replace").strip()
        if err.code == 400:
            raise DiagramError(f"the Mermaid source is invalid: {detail or 'syntax error'}") from err
        logger.warning("Diagram renderer answered HTTP %s: %s", err.code, detail)
        raise DiagramError("the diagram renderer is unavailable right now") from err
    except Exception as err:
        logger.warning("Diagram renderer could not be reached", exc_info=True)
        raise DiagramError("the diagram renderer is unavailable right now") from err
    if len(png) > MAX_PNG_BYTES:
        raise DiagramError("the diagram is too large; draw a smaller one")
    if not png.startswith(_PNG_MAGIC):
        raise DiagramError("the diagram renderer didn't return an image")
    return _on_white(png)


def _on_white(png: bytes) -> bytes:
    """Kroki's PNG has a transparent background, so on Slack's dark theme the dark labels
    and lines vanish. Flatten it onto white. Falls back to the original if that fails."""
    try:
        with Image.open(io.BytesIO(png)) as image:
            image = image.convert("RGBA")
            flat = Image.new("RGB", image.size, "white")
            flat.paste(image, mask=image.getchannel("A"))
            out = io.BytesIO()
            flat.save(out, "PNG", optimize=True)
            return out.getvalue()
    except Exception:  # PIL raises many types for corrupt input
        logger.warning("Could not put the diagram on a white background", exc_info=True)
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
