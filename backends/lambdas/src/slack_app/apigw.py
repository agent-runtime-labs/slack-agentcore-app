"""Helpers for API Gateway HTTP API (payload format 2.0) events and responses."""

import base64
import json
from typing import Any


def raw_body(event: dict) -> str:
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    return body


def header(event: dict, name: str) -> str | None:
    # HTTP API v2 lower-cases header names.
    return (event.get("headers") or {}).get(name.lower())


def query_param(event: dict, name: str) -> str | None:
    return (event.get("queryStringParameters") or {}).get(name)


def cookie(event: dict, name: str) -> str | None:
    for item in event.get("cookies") or []:
        key, _, value = item.partition("=")
        if key.strip() == name:
            return value.strip()
    return None


def json_response(status: int, body: Any) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def html_response(status: int, html: str, cookies: list[str] | None = None) -> dict:
    response = {
        "statusCode": status,
        "headers": {
            "Content-Type": "text/html; charset=utf-8",
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
        },
        "body": html,
    }
    if cookies:
        response["cookies"] = cookies
    return response


def redirect(location: str, cookies: list[str] | None = None) -> dict:
    response = {
        "statusCode": 302,
        "headers": {"Location": location, "Cache-Control": "no-store"},
        "body": "",
    }
    if cookies:
        response["cookies"] = cookies
    return response
