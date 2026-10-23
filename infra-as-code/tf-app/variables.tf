variable "env" {
  type        = string
  description = "Environment name (dev, prod, ...)"
}

variable "region" {
  type = string
}

variable "project_name" {
  type        = string
  description = "Used as a prefix for resource names (lowercase, dashes)"
  default     = "slack-agentcore"
}

variable "default_tags" {
  type    = map(string)
  default = {}
}

variable "model_id" {
  type        = string
  description = "Bedrock model or inference profile ID for the chat agent. It reads the whole Slack thread and judges when to ask, answer or keep it short, which Nova Micro (the lowest-cost option) didn't do reliably."
  default     = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "github_model_id" {
  type        = string
  description = "Bedrock model or inference profile ID for the GitHub MCP sub-agent, which needs stronger tool-use and a larger max_tokens than the low-cost chat model."
  default     = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "triage_model_id" {
  type        = string
  description = "Bedrock model the agent-worker Lambda asks what to do with a channel message without an @mention: reply, react, correct or ignore, given the thread so far."
  default     = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "linkedin_provider_name" {
  type        = string
  description = "AgentCore Identity OAuth2 credential provider (created by scripts/identity-setup.sh)"
  default     = "slack-agent-linkedin"
}

variable "github_provider_name" {
  type        = string
  description = "AgentCore Identity OAuth2 credential provider (created by scripts/identity-setup.sh)"
  default     = "slack-agent-github"
}

variable "statuspulse_mcp_url" {
  type        = string
  description = <<-EOT
    Streamable-HTTP URL of a public status MCP server such as StatusPulse (see
    docs/status-mcp.md), e.g. https://status.example.com/mcp. It needs no sign-in. The agent
    uses it for the check_service_status tool and replies with Slack Block Kit cards.
    Empty disables the tool.
  EOT
  default     = ""

  validation {
    condition     = var.statuspulse_mcp_url == "" || can(regex("^https://", var.statuspulse_mcp_url))
    error_message = "statuspulse_mcp_url must be empty or an https:// URL."
  }
}

variable "cimd_providers" {
  type        = list(string)
  description = <<-EOT
    CIMD remote MCP servers to enable, by registry key (see
    backends/agents/slack_agent/src/cimd/providers.py). These need no client ID, no
    client secret and no credential provider: the app identifies itself with the URL of
    its client metadata document. Empty disables the CIMD tools entirely.
  EOT
  default     = ["linear", "notion"]
}

variable "cimd_model_id" {
  type        = string
  description = "Bedrock model for the CIMD sub-agents, which chain tool calls through remote MCP catalogues."
  default     = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "cimd_connection_ttl_days" {
  type        = number
  description = "Days a CIMD connection survives unused before DynamoDB TTL drops it and the user must reconnect."
  default     = 90
}

variable "knowledge_enabled" {
  type        = bool
  description = <<-EOT
    Team knowledge memory: summarise the threads in the internal channels the bot is in
    (S3 Vectors) and give the agent a search_past_threads tool to find and cite them.
    Needs the channels:read, groups:read and users:read scopes and the channel_left and
    group_left events (docs/slack-setup.md). Off leaves the app exactly as without it.
  EOT
  default     = false
}

variable "knowledge_summary_model_id" {
  type        = string
  description = <<-EOT
    Bedrock model the knowledge indexer summarises each quiet thread with (one InvokeModel
    call per thread). Working out which suggestion actually fixed a problem and splitting a
    thread into its problems is reading judgment, so this defaults to Claude Sonnet 5.5,
    through its global cross-Region inference profile (bedrock-runtime doesn't take the
    bare model ID). Enable it under Bedrock model access first; until then the fallback
    model is used.
  EOT
  default     = "global.anthropic.claude-sonnet-5-5"
}

variable "knowledge_summary_fallback_model_id" {
  type        = string
  description = "Bedrock model used when the summary model declines a thread or can't be used (no model access, IAM). Empty for none."
  default     = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "knowledge_embedding_model_id" {
  type        = string
  description = "Embedding model for summaries and search queries. The index is 1024-dimensional, so it must produce that (Titan Text Embeddings v2 does)."
  default     = "amazon.titan-embed-text-v2:0"
}

variable "knowledge_excluded_channels" {
  type        = list(string)
  description = "Channel IDs never indexed, e.g. [\"C0123ABCD\"]. Vectors already stored for them go at the next daily sweep."
  default     = []
}

variable "log_level" {
  type    = string
  default = "INFO"
}

variable "agent_image_tag" {
  type        = string
  description = "Pre-built agent image tag (CI). Empty = build locally from source."
  default     = ""
}

variable "lambda_image_tag" {
  type        = string
  description = "Pre-built Lambda image tag (CI). Empty = build locally from source."
  default     = ""
}

variable "api_throttle_rate_limit" {
  type        = number
  description = "Steady-state requests per second allowed on the public API"
  default     = 20
}

variable "api_throttle_burst_limit" {
  type    = number
  default = 40
}
