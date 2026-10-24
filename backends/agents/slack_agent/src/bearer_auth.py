"""Bearer auth for the remote MCP servers the nested agents talk to (GitHub and the CIMD providers).

The MCP client reports every HTTP error status with the same generic exception text, so whether
a server rejected the user's token (401) is only visible here, on the response itself.
"""

import httpx


class BearerAuth(httpx.Auth):
    """Sends the user's token and remembers whether the server rejected it with a 401."""

    def __init__(self, access_token: str):
        self._access_token = access_token
        self.unauthorized = False

    def auth_flow(self, request):
        request.headers["Authorization"] = f"Bearer {self._access_token}"
        response = yield request
        if response.status_code == 401:
            self.unauthorized = True
