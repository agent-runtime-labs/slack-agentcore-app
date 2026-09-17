"""How Slack users map to AgentCore identities and sessions.

The runtime user ID is the key AgentCore Identity uses in the token vault, so it
MUST be unique per human. Slack user IDs are only unique within a workspace,
hence the team ID prefix.
"""

import hashlib


def runtime_user_id(team_id: str, slack_user_id: str) -> str:
    if not team_id or not slack_user_id:
        raise ValueError("team_id and slack_user_id are required")
    return f"slack-{team_id}-{slack_user_id}"


def runtime_session_id(team_id: str, channel: str, thread_ts: str, slack_user_id: str) -> str:
    """One AgentCore session per (thread, user).

    Including the user keeps conversation history private per person even when
    several people talk to the bot in the same thread. AgentCore requires at
    least 33 characters; a SHA-256 hex digest is 64.
    """
    key = "|".join([team_id, channel, thread_ts, slack_user_id])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()
