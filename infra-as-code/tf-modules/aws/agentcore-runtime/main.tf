# ============================================================================
# AgentCore Runtime (container) + execution role
# ============================================================================

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region
  arn_prefix = "arn:aws:bedrock-agentcore:${local.region}:${local.account_id}"
}

resource "aws_bedrockagentcore_agent_runtime" "this" {
  agent_runtime_name = var.name
  description        = var.description
  role_arn           = aws_iam_role.execution.arn

  agent_runtime_artifact {
    container_configuration {
      container_uri = var.image_uri
    }
  }

  # No inbound JWT authorizer: the runtime is IAM-only and just the worker
  # Lambda role may invoke it (and act for a user via runtimeUserId).
  network_configuration {
    network_mode = "PUBLIC"
  }

  lifecycle_configuration {
    idle_runtime_session_timeout = var.idle_session_timeout_seconds
    max_lifetime                 = var.max_session_lifetime_seconds
  }

  environment_variables = merge(
    {
      AWS_REGION         = local.region
      AWS_DEFAULT_REGION = local.region
    },
    var.environment_variables
  )

  depends_on = [aws_iam_role_policy.execution]
}

# AgentCore creates a workload identity for the runtime. The OAuth return URL
# (our /oauth2/callback) must be allow-listed on it, and Terraform has no
# attribute for that on an auto-created identity, so use the API directly.
resource "terraform_data" "allowed_return_urls" {
  count = length(var.allowed_oauth2_return_urls) > 0 ? 1 : 0

  triggers_replace = [
    local.workload_identity_name,
    join(",", var.allowed_oauth2_return_urls),
  ]

  provisioner "local-exec" {
    interpreter = ["/bin/bash", "-c"]
    command     = <<-EOT
      set -euo pipefail
      aws bedrock-agentcore-control update-workload-identity \
        --region "${local.region}" \
        --name "${local.workload_identity_name}" \
        --allowed-resource-oauth2-return-urls ${join(" ", formatlist("%q", var.allowed_oauth2_return_urls))} \
        >/dev/null
      echo "Allowed OAuth2 return URLs set on ${local.workload_identity_name}"
    EOT
  }
}

locals {
  workload_identity_arn  = aws_bedrockagentcore_agent_runtime.this.workload_identity_details[0].workload_identity_arn
  workload_identity_name = element(split("/", local.workload_identity_arn), length(split("/", local.workload_identity_arn)) - 1)
}
