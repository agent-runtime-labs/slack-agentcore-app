"""Minimal JSON/form HTTP helpers built on urllib.

The agent image deliberately carries no HTTP client of its own (see requirements.txt),
and these three functions are all the CIMD flow needs. Everything is https-only: an
authorization server or token endpoint reached over plain http would leak the
authorization code and the access token.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

# Several providers (Notion among them) sit behind a CDN that answers 403 to urllib's
# default "Python-urllib/3.x" User-Agent. Always send our own.
USER_AGENT = "slack-agentcore-app/1.0 (+https://github.com/aws-samples/slack-agentcore-app)"
TIMEOUT_SECONDS = 10
# Authorization server metadata is small by design; the CIMD draft suggests a 5 KB
# ceiling for client documents and real metadata documents are the same order. Cap
# the read so a hostile or broken endpoint can't stream us out of memory.
MAX_RESPONSE_BYTES = 256 * 1024


class HttpError(RuntimeError):
    """Non-2xx response. `payload` holds the decoded body when it was JSON."""

    def __init__(self, url: str, status: int, payload: dict | str):
        super().__init__(f"{url} returned HTTP {status}")
        self.url = url
        self.status = status
        self.payload = payload


def require_https(url: str, what: str) -> str:
    if not url.startswith("https://"):
        raise ValueError(f"{what} must be an https URL, got {url!r}")
    return url


def get_json(url: str) -> dict:
    """GET a JSON document. Raises HttpError on a non-2xx response."""
    require_https(url, "URL")
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    return _send(request, url)


def post_form(url: str, fields: dict[str, str]) -> dict:
    """POST application/x-www-form-urlencoded and decode the JSON response.

    OAuth token endpoints report failures as a 400 with a JSON body ({"error": ...}),
    so callers get those through HttpError.payload rather than as an opaque status.
    """
    require_https(url, "Token endpoint")
    body = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    return _send(request, url)


def _send(request: urllib.request.Request, url: str) -> dict:
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # nosec B310 - https enforced above
            return _decode(url, response.status, response.read(MAX_RESPONSE_BYTES))
    except urllib.error.HTTPError as err:
        body = err.read(MAX_RESPONSE_BYTES)
        raise HttpError(url, err.code, _safe_json(body)) from None


def _decode(url: str, status: int, body: bytes) -> dict:
    payload = _safe_json(body)
    if not isinstance(payload, dict):
        raise HttpError(url, status, payload)
    return payload


def _safe_json(body: bytes) -> dict | str:
    try:
        return json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return body[:200].decode("utf-8", "replace")
