"""Local stand-in for API Gateway + Lambda, used by Tilt.

Translates HTTP requests into API Gateway HTTP API (v2) events and calls the
same handler functions that run in AWS.
"""

from fastapi import FastAPI, Request, Response
from starlette.concurrency import run_in_threadpool

from slack_app.config import is_local
from slack_app.handlers import oauth_callback, slack_events

if not is_local():
    raise RuntimeError("local_server is for local development only (set APP_ENV=local)")

app = FastAPI(title="slack-agentcore local gateway")


def _to_event(request: Request, body: bytes) -> dict:
    return {
        "version": "2.0",
        "rawPath": request.url.path,
        "rawQueryString": request.url.query,
        "headers": {k.lower(): v for k, v in request.headers.items()},
        "queryStringParameters": dict(request.query_params) or None,
        "cookies": [f"{k}={v}" for k, v in request.cookies.items()],
        "body": body.decode("utf-8"),
        "isBase64Encoded": False,
        "requestContext": {"http": {"method": request.method, "path": request.url.path}},
    }


def _to_response(result: dict) -> Response:
    response = Response(
        content=result.get("body", ""),
        status_code=result.get("statusCode", 200),
        headers=result.get("headers") or {},
    )
    for value in result.get("cookies") or []:
        response.headers.append("set-cookie", value)
    return response


async def _invoke(handler, request: Request) -> Response:
    event = _to_event(request, await request.body())
    result = await run_in_threadpool(handler, event, None)
    return _to_response(result)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.post("/slack/events")
async def slack_events_route(request: Request) -> Response:
    return await _invoke(slack_events.handler, request)


@app.get("/oauth2/start")
@app.get("/oauth2/callback")
async def oauth_routes(request: Request) -> Response:
    return await _invoke(oauth_callback.handler, request)
