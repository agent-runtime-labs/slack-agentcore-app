variable "name" {
  type        = string
  description = "Runtime name (letters, digits, underscores)"

  validation {
    condition     = can(regex("^[a-zA-Z][a-zA-Z0-9_]{0,47}$", var.name))
    error_message = "Must start with a letter; max 48 chars; letters, digits and underscores only."
  }
}

variable "description" {
  type    = string
  default = "AgentCore Runtime agent"
}

variable "image_uri" {
  type        = string
  description = "linux/arm64 container image URI"
}

variable "ecr_repository_arn" {
  type = string
}

variable "environment_variables" {
  type    = map(string)
  default = {}
}

variable "model_resource_arns" {
  type        = list(string)
  description = "Bedrock foundation-model / inference-profile ARNs the agent may invoke"
}

variable "oauth2_credential_provider_names" {
  type        = list(string)
  description = "AgentCore Identity OAuth2 credential providers the agent may fetch user tokens from"
}

variable "additional_policy_statements" {
  type        = list(any)
  description = <<-EOT
    Extra IAM policy statements for the execution role, for resources this module knows
    nothing about (e.g. a DynamoDB table holding OAuth tokens the agent manages itself).
  EOT
  default     = []
}

variable "allowed_oauth2_return_urls" {
  type        = list(string)
  description = "URLs AgentCore Identity may redirect to after user consent"
  default     = []
}

variable "idle_session_timeout_seconds" {
  type    = number
  default = 900
}

variable "max_session_lifetime_seconds" {
  type    = number
  default = 28800
}
