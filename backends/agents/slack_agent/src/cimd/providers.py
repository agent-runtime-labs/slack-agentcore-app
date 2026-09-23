"""The CIMD provider registry -- the only file you edit to add a remote MCP server.

Everything else in this package is provider-agnostic: discovery, PKCE, the token
store, the tool wrapper, the Lambda that completes consent, and the Terraform.

To add a provider:

  1. Check the server actually supports CIMD:
         curl -s https://<host>/.well-known/oauth-authorization-server | jq
     and look for `"client_id_metadata_document_supported": true`.
  2. Add a `CimdProvider(...)` entry below.
  3. Add its key to `cimd_providers` in infra-as-code/tf-vars/<env>/app-infra-params.tfvars
     (or to CIMD_PROVIDERS in .env for local development).

Verified as CIMD-capable at the time of writing: mcp.linear.app, mcp.notion.com,
mcp.sentry.dev, mcp.canva.com, huggingface.co. Webflow and PayPal advertise
`false` and still need Dynamic Client Registration, so they don't belong here.
"""

from dataclasses import dataclass, field

import config


@dataclass(frozen=True)
class CimdProvider:
    """One remote MCP server reachable with a per-user OAuth token obtained via CIMD."""

    key: str
    """Stable short id. Used as the DynamoDB sort key, the tool-name suffix and the
    toggle in CIMD_PROVIDERS, so changing it disconnects every existing user."""

    display_name: str
    """Shown to the user in Slack ("Connect Linear")."""

    mcp_url: str
    """Streamable-HTTP MCP endpoint. Discovery starts here: we read its protected
    resource metadata (RFC 9728) to find the authorization server."""

    scope: str
    """Space-delimited OAuth scopes to request. Keep it minimal -- the user sees these
    on the consent screen. Empty means "ask for whatever the server defaults to"."""

    summary: str
    """Half a sentence naming the things this server can do, e.g. "issues, projects and
    cycles". It is interpolated into both the tool description the chat model sees and
    the Slack-facing prompts, so write it as a noun phrase."""

    guidance: str = ""
    """Optional extra system-prompt text for this provider's sub-agent: quirks worth
    knowing, tools that are easy to miss, wording to avoid. Appended to the shared
    prompt in tool.py."""

    authorization_server: str = ""
    """Override for servers whose protected resource metadata is missing or wrong.
    Normally left empty so discovery decides."""

    aliases: tuple[str, ...] = field(default_factory=tuple)
    """Other words a user might say for this product, folded into the tool description
    so the chat model routes "my tickets" or "my wiki" to the right tool."""

    @property
    def tool_name(self) -> str:
        """The name the chat model calls, e.g. "use_linear"."""
        return f"use_{self.key}"


LINEAR = CimdProvider(
    key="linear",
    display_name="Linear",
    mcp_url="https://mcp.linear.app/mcp",
    # Linear advertises read, write, openid and email. We ask only for what the tools
    # need; `write` still goes through the confirm-before-writing rule in tool.py.
    scope="read write",
    summary="issues, projects, cycles, teams and comments",
    aliases=("tickets", "sprints", "backlog"),
    guidance=(
        "Linear organises work as issues inside teams, projects and cycles. Issue identifiers "
        "look like ENG-123 -- when the user gives one, use it directly instead of searching. "
        "Statuses are per-team workflow states, so never assume a status name exists; read it "
        "back from the issue or the team's workflow states."
    ),
)

NOTION = CimdProvider(
    key="notion",
    display_name="Notion",
    mcp_url="https://mcp.notion.com/mcp",
    # Notion's authorization server exposes a single coarse scope named "default".
    scope="default",
    summary="pages, databases and their content",
    aliases=("wiki", "docs", "notes"),
    guidance=(
        "Notion content is reachable only if the user shared it with the connected integration, "
        "so an empty search result means 'not shared with me', not 'does not exist' -- say that "
        "rather than claiming the page is missing. Prefer searching by title first, then fetch the "
        "page by id. Page content is blocks: quote what you read instead of paraphrasing structure."
    ),
)

# Every provider this build knows about. Membership here does not enable it --
# CIMD_PROVIDERS does (see enabled_providers).
PROVIDERS: dict[str, CimdProvider] = {p.key: p for p in (LINEAR, NOTION)}


def enabled_providers() -> list[CimdProvider]:
    """Providers switched on for this deployment, in the order given by CIMD_PROVIDERS.

    Unknown keys are ignored rather than fatal: a stale environment variable should not
    take the whole agent down, and the mismatch is visible in the startup log.
    """
    if not config.CIMD_TOKEN_TABLE:
        # Without somewhere to keep user tokens there is no point offering the tools.
        return []
    return [PROVIDERS[key] for key in config.CIMD_PROVIDERS if key in PROVIDERS]
