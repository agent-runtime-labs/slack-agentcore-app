"""Agent configuration (environment variables set by Terraform or Tilt)."""

import os

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

# Amazon Nova Micro: the lowest-cost Bedrock text model with tool use.
MODEL_ID = os.getenv("MODEL_ID", "us.amazon.nova-micro-v1:0")

# AgentCore Identity OAuth2 credential provider (created by infra-as-code/scripts/identity-setup.sh).
LINKEDIN_PROVIDER_NAME = os.getenv("LINKEDIN_PROVIDER_NAME", "slack-agent-linkedin")

# Where AgentCore Identity sends the browser after LinkedIn consent (our /oauth2/callback).
# Must be listed in the workload identity's allowed return URLs.
OAUTH2_RETURN_URL = os.getenv("OAUTH2_RETURN_URL", "")

# Local development only: there is no Runtime to mint workload access tokens, so the
# agent asks for one itself using this workload identity.
LOCAL_WORKLOAD_NAME = os.getenv("LOCAL_WORKLOAD_NAME", "")

MAX_CACHED_CONVERSATIONS = int(os.getenv("MAX_CACHED_CONVERSATIONS", "200"))
