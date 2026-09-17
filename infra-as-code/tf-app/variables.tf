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
  description = "Bedrock model or inference profile ID. Nova Micro is the lowest-cost option."
  default     = "us.amazon.nova-micro-v1:0"
}

variable "github_model_id" {
  type        = string
  description = "Bedrock model or inference profile ID for the GitHub MCP sub-agent, which needs stronger tool-use and a larger max_tokens than the low-cost chat model."
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
