variable "name" {
  type = string
}

variable "description" {
  type    = string
  default = ""
}

variable "image_uri" {
  type = string
}

variable "command" {
  type        = list(string)
  description = "Handler, e.g. [\"slack_app.handlers.slack_events.handler\"]"
}

variable "timeout" {
  type    = number
  default = 30
}

variable "memory_size" {
  type    = number
  default = 256
}

variable "environment_variables" {
  type    = map(string)
  default = {}
}

variable "policy_json" {
  type        = string
  description = "Inline IAM policy for the function's own permissions"
  default     = null
}

variable "log_retention_days" {
  type    = number
  default = 30
}
