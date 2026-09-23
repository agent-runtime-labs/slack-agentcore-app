"""Agent configuration (environment variables set by Terraform or Tilt)."""

import os

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

# Amazon Nova Micro: the lowest-cost Bedrock text model with tool use.
MODEL_ID = os.getenv("MODEL_ID", "us.amazon.nova-micro-v1:0")

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

MAX_CACHED_CONVERSATIONS = int(os.getenv("MAX_CACHED_CONVERSATIONS", "200"))

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
