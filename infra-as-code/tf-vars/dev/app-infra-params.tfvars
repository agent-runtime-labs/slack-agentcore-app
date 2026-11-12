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

# draw_diagram: Kroki server that renders the agent's Mermaid to PNG. Empty disables the tool.
# kroki.io is the public instance, so diagram source leaves for a third party: fine for
# testing, but point this at a Kroki you run for anything private. Needs the files:write
# Slack scope (docs/slack-setup.md).
diagram_render_url = "https://kroki.io"

# Team knowledge memory: find and cite earlier Slack threads (see docs/architecture.md).
# Needs extra Slack scopes and events first (docs/slack-setup.md).
knowledge_enabled           = true
knowledge_excluded_channels = []

default_tags = {
  project     = "slack-agentcore-app"
  environment = "dev"
  deployment  = "terraform"
}
