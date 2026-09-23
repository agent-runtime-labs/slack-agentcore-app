"""CIMD-based per-user access to remote MCP servers (Linear, Notion, ...).

Two OAuth worlds live side by side in this agent:

  * **AgentCore Identity** (linkedin.py, github.py) -- AWS is the OAuth client, holds the
    client secret, and owns the token vault. Adding a provider means registering an app
    with the vendor and creating a credential provider (scripts/identity-setup.sh).

  * **CIMD** (this package) -- "Client ID Metadata Document",
    draft-ietf-oauth-client-id-metadata-document, adopted by MCP as SEP-991 and the
    preferred replacement for Dynamic Client Registration since the 2025-11-25 spec.
    There is no registration and no client secret: our `client_id` *is* an HTTPS URL
    that serves a small JSON document describing this app, the authorization server
    fetches that URL on demand, and we own the resulting user tokens (tokens.py).

Why not do Linear/Notion through AgentCore Identity? Its credential providers require
CLIENT_SECRET_BASIC/POST, AWS_IAM_ID_TOKEN_JWT or PRIVATE_KEY_JWT. CIMD forbids shared
secrets (draft section 4.1) and these servers advertise only `none` + PKCE, so no
AgentCore client authentication method fits. We therefore act as the OAuth client
ourselves, reusing the Slack connect-link plumbing that already exists.

Adding another CIMD server is a single entry in providers.py -- no Lambda change, no
Terraform change, no secret anywhere. See docs/cimd-providers.md.
"""

from cimd.providers import PROVIDERS, CimdProvider, enabled_providers
from cimd.tool import build_cimd_tools

__all__ = ["PROVIDERS", "CimdProvider", "enabled_providers", "build_cimd_tools"]
