"""Agent configuration (environment variables set by Terraform or Tilt)."""

import os

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

# Claude Haiku 4.5: reading a whole Slack thread and judging when to ask, answer or stay
# brief needs more than Nova Micro (the lowest-cost model with tool use) reliably gave.
MODEL_ID = os.getenv("MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")

# Claude Haiku 4.5: used only for the GitHub MCP sub-agent, which chains multiple tool calls
# through GitHub's large tool catalog -- Nova Micro was hitting MaxTokensReachedException on it.
GITHUB_MODEL_ID = os.getenv("GITHUB_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")

# AgentCore Identity OAuth2 credential providers (created by infra-as-code/scripts/identity-setup.sh).
LINKEDIN_PROVIDER_NAME = os.getenv("LINKEDIN_PROVIDER_NAME", "slack-agent-linkedin")
GITHUB_PROVIDER_NAME = os.getenv("GITHUB_PROVIDER_NAME", "slack-agent-github")

# Where AgentCore Identity sends the browser after consent (our /oauth2/callback).
# Must be listed in the workload identity's allowed return URLs.
# CIMD providers reuse it as their OAuth redirect_uri, so it must also appear in
# `redirect_uris` of the client metadata document served by the oauth_callback Lambda.
OAUTH2_RETURN_URL = os.getenv("OAUTH2_RETURN_URL", "")

# Local development only: there is no Runtime to mint workload access tokens, so the
# agent asks for one itself using this workload identity.
LOCAL_WORKLOAD_NAME = os.getenv("LOCAL_WORKLOAD_NAME", "")

# --- CIMD remote MCP servers (see cimd/providers.py and docs/cimd-providers.md) ------

# Which providers from the registry are switched on, e.g. "linear,notion".
CIMD_PROVIDERS = [key.strip() for key in os.getenv("CIMD_PROVIDERS", "").split(",") if key.strip()]

# DynamoDB table holding each user's CIMD tokens. Empty disables every CIMD tool,
# which is what local development does unless you point it at a real dev table.
CIMD_TOKEN_TABLE = os.getenv("CIMD_TOKEN_TABLE", "")

# Our OAuth client_id: the https URL of the client metadata document served at
# <PUBLIC_BASE_URL>/oauth2/client-metadata.json. This *is* the registration.
CIMD_CLIENT_ID = os.getenv("CIMD_CLIENT_ID", "")

# CIMD sub-agents chain tool calls through large remote catalogues, same as GitHub's.
CIMD_MODEL_ID = os.getenv("CIMD_MODEL_ID", GITHUB_MODEL_ID)

# How long a connection survives without being used before DynamoDB's TTL removes it
# and the user is asked to reconnect.
CIMD_CONNECTION_TTL_DAYS = int(os.getenv("CIMD_CONNECTION_TTL_DAYS", "90"))

# --- Public service status (see status_mcp.py and docs/status-mcp.md) ---------------

# The streamable-HTTP URL of a status MCP server such as StatusPulse, e.g.
# https://status.example.com/mcp. It is public and needs no sign-in. Empty disables
# the check_service_status tool.
STATUSPULSE_MCP_URL = os.getenv("STATUSPULSE_MCP_URL", "").strip()

# --- Team knowledge memory (see past_threads.py and docs/architecture.md) -------------

# The S3 Vectors index the knowledge indexer writes thread summaries to. Empty disables
# search_past_threads, whatever the payload says: S3 Vectors has no local emulator, so
# under Tilt this stays empty unless pointed at a real dev bucket.
KNOWLEDGE_VECTOR_BUCKET = os.getenv("KNOWLEDGE_VECTOR_BUCKET", "")
KNOWLEDGE_INDEX = os.getenv("KNOWLEDGE_INDEX", "threads")

# Must match the model the indexer embeds summaries with (1024 dimensions, normalised).
KNOWLEDGE_EMBEDDING_MODEL_ID = os.getenv("KNOWLEDGE_EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0")

# Cosine similarity below which a past thread isn't worth showing the model.
KNOWLEDGE_MIN_SIMILARITY = float(os.getenv("KNOWLEDGE_MIN_SIMILARITY", "0.35"))
