"""Shared pending-consent side-channel for OAuth2 tools (LinkedIn, GitHub, ...).

A tool sets this when it needs the user to connect an account; main.py reads it
back after the agent loop to decide whether to send a private connect link.
"""

from dataclasses import dataclass


@dataclass
class AuthState:
    """Collects a pending consent request during one invocation."""

    provider: str | None = None
    authorization_url: str | None = None
    session_uri: str | None = None

    def as_dict(self) -> dict | None:
        if not self.authorization_url:
            return None
        return {"provider": self.provider, "authorizationUrl": self.authorization_url, "sessionUri": self.session_uri}
