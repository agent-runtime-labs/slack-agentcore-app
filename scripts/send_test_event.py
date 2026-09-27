"""Send a correctly signed, fake Slack `app_mention` event to the local server.

    python scripts/send_test_event.py --text "What is my LinkedIn name?"
    python scripts/send_test_event.py --channel-message --thread-ts <ts> --text "And my GitHub?"
    python scripts/send_test_event.py --text "Why is this failing?" --file error.png=F0123ABCDEF
    python scripts/send_test_event.py --dm --text "" --file invoice.pdf

--file attaches a file reference, as Slack does when someone uploads one. With a real
file ID from your workspace (and SLACK_BOT_TOKEN set for Tilt), the agent downloads and
reads it; without one it gets a made-up ID and tells you it couldn't open the file.

Uses SLACK_SIGNING_SECRET from the environment (Tilt's default is
"local-dev-signing-secret"). With SLACK_DRY_RUN=true the replies, including the
private "Connect LinkedIn" link, show up in the slack-app logs in Tilt.
"""

import argparse
import hashlib
import hmac
import json
import mimetypes
import os
import time
import urllib.error
import urllib.request
import uuid


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--text", default="Hello! What can you do?")
    parser.add_argument("--user", default="ULOCALDEV1", help="Fake Slack user ID (each ID gets its own LinkedIn token)")
    parser.add_argument("--team", default="TLOCALDEV1")
    parser.add_argument("--channel", default="CLOCALDEV1")
    parser.add_argument("--thread-ts", default=None, help="Reuse to continue a conversation")
    kind = parser.add_mutually_exclusive_group()
    kind.add_argument("--dm", action="store_true", help="Send as a direct message instead of a mention")
    kind.add_argument(
        "--channel-message",
        action="store_true",
        help="Send as a plain channel message without an @mention (answered in threads the bot is in, else triaged)",
    )
    parser.add_argument(
        "--file",
        action="append",
        default=[],
        metavar="NAME[=FILE_ID]",
        help="Attach a file reference; repeat for several. FILE_ID is a real Slack file ID (F...)",
    )
    parser.add_argument("--url", default="http://localhost:8081/slack/events")
    args = parser.parse_args()

    if not args.url.startswith(("http://localhost", "http://127.0.0.1")):
        raise SystemExit("This helper only targets the local server.")

    ts = f"{time.time():.6f}"
    plain = args.dm or args.channel_message
    event = {
        "type": "message" if plain else "app_mention",
        "user": args.user,
        "text": args.text if plain else f"<@UBOTLOCAL> {args.text}",
        "channel": args.channel,
        "ts": ts,
        "team": args.team,
    }
    if plain:
        event["channel_type"] = "im" if args.dm else "channel"
    if args.thread_ts:
        event["thread_ts"] = args.thread_ts
    if args.file:
        event["files"] = [_file(spec, index) for index, spec in enumerate(args.file)]
        if plain:
            event["subtype"] = "file_share"

    body = json.dumps({
        "type": "event_callback",
        "team_id": args.team,
        "event_id": f"Ev{uuid.uuid4().hex[:10]}",
        "event": event,
        "authorizations": [{"team_id": args.team, "user_id": "UBOTLOCAL", "is_bot": True}],
    })
    secret = os.getenv("SLACK_SIGNING_SECRET", "local-dev-signing-secret")
    stamp = str(int(time.time()))
    signature = "v0=" + hmac.new(secret.encode(), f"v0:{stamp}:{body}".encode(), hashlib.sha256).hexdigest()

    request = urllib.request.Request(
        args.url,
        data=body.encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Slack-Request-Timestamp": stamp,
            "X-Slack-Signature": signature,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # nosec B310 - localhost only
            print(response.status, response.read().decode())
    except urllib.error.HTTPError as err:
        raise SystemExit(f"{err.code} {err.read().decode()}") from None
    print(f"thread_ts={args.thread_ts or ts}  (pass --thread-ts to continue this conversation)")
    print("Watch the slack-app logs in Tilt for the agent's reply.")


def _file(spec: str, index: int) -> dict:
    name, _, file_id = spec.partition("=")
    return {
        "id": file_id or f"FLOCALDEV{index}",
        "name": name,
        "mimetype": mimetypes.guess_type(name)[0] or "application/octet-stream",
    }


if __name__ == "__main__":
    main()
