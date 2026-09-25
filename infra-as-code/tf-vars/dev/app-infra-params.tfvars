env          = "dev"
region       = "us-east-1"
project_name = "slack-agentcore"

model_id               = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
github_model_id        = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
triage_model_id        = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
linkedin_provider_name = "slack-agent-linkedin"
github_provider_name   = "slack-agent-github"
log_level              = "INFO"

# Remote MCP servers reached with CIMD (no client ID, no secret, no credential provider).
# Keys come from backends/agents/slack_agent/src/cimd/providers.py.
cimd_providers = ["linear", "notion"]
cimd_model_id  = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

default_tags = {
  project     = "slack-agentcore-app"
  environment = "dev"
  deployment  = "terraform"
}
