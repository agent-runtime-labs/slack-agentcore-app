"""Shared pending-consent side-channel for OAuth2 tools (LinkedIn, GitHub, Linear, ...).

A tool sets this when it needs the user to connect an account; main.py reads it
back after the agent loop to decide whether to send a private connect link.

Two kinds of consent end up here, and the oauth_callback Lambda tells them apart by
which field is populated:

  * AgentCore Identity (LinkedIn, GitHub) -> `session_uri`. AWS owns the flow; the
    callback only has to confirm the session belongs to this user.
  * CIMD (Linear, Notion, ...)            -> `cimd`. We own the flow; the callback
    exchanges the authorization code itself. See docs/cimd-providers.md for the
    payload contract.
"""

from dataclasses import dataclass


@dataclass
class AuthState:
    """Collects a pending consent request during one invocation."""

    provider: str | None = None
    authorization_url: str | None = None
    session_uri: str | None = None
    cimd: dict | None = None
    kubernetes: dict | None = None

    def request(self, provider: str, authorization_url: str, *, session_uri=None, cimd=None, kubernetes=None) -> None:
        """Select exactly one pending flow, clearing prior tools' consent fields."""
        self.provider, self.authorization_url = provider, authorization_url
        self.session_uri, self.cimd, self.kubernetes = session_uri, cimd, kubernetes

    def as_dict(self) -> dict | None:
        if not self.authorization_url:
            return None
        return {
            "provider": self.provider,
            "authorizationUrl": self.authorization_url,
            "sessionUri": self.session_uri,
            "cimd": self.cimd,
            **({"kubernetes": self.kubernetes} if self.kubernetes else {}),
        }
