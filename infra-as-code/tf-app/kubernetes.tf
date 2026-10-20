# Optional remote Kubernetes integration. No local cluster resources live in this repo.
variable "kubernetes" {
  description = "Dedicated Pinniped connection; null disables Kubernetes tools. Secret value is populated out-of-band as JSON {client_secret}."
  type = object({
    cluster_id         = string
    issuer_url         = string
    issuer_ca_b64      = optional(string, "")
    audience           = string
    concierge_url      = string
    concierge_ca_b64   = string
    authenticator_name = string
    oidc_client_id     = string
    oidc_secret_arn    = string
  })
  default = null
  validation {
    condition = var.kubernetes == null ? true : (
      can(regex("^https://", var.kubernetes.issuer_url)) &&
      can(regex("^https://", var.kubernetes.concierge_url)) &&
      startswith(var.kubernetes.oidc_client_id, "client.oauth.pinniped.dev-") &&
      startswith(var.kubernetes.oidc_secret_arn, "arn:aws:secretsmanager:")
    )
    error_message = "Kubernetes endpoints require HTTPS, a dedicated Pinniped client ID, and an existing Secrets Manager secret ARN."
  }
}

resource "aws_dynamodb_table" "kubernetes_tokens" {
  count        = var.kubernetes == null ? 0 : 1
  name         = "${local.name_prefix}-kubernetes-tokens"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "user_id"
  range_key    = "cluster"
  attribute {
    name = "user_id"
    type = "S"
  }
  attribute {
    name = "cluster"
    type = "S"
  }
  ttl {
    attribute_name = "ttl"
    enabled        = true
  }
  server_side_encryption {
    enabled = true
  }
  point_in_time_recovery {
    enabled = false
  }
}

locals {
  k8s_env = var.kubernetes == null ? {} : {
    K8S_ENABLED            = "true"
    K8S_CLUSTER_ID         = var.kubernetes.cluster_id
    K8S_ISSUER_URL         = var.kubernetes.issuer_url
    K8S_ISSUER_CA_B64      = var.kubernetes.issuer_ca_b64
    K8S_AUDIENCE           = var.kubernetes.audience
    K8S_CONCIERGE_URL      = var.kubernetes.concierge_url
    K8S_CONCIERGE_CA_B64   = var.kubernetes.concierge_ca_b64
    K8S_AUTHENTICATOR_NAME = var.kubernetes.authenticator_name
    K8S_OIDC_CLIENT_ID     = var.kubernetes.oidc_client_id
    K8S_OIDC_SECRET_ARN    = var.kubernetes.oidc_secret_arn
    K8S_TOKEN_TABLE        = aws_dynamodb_table.kubernetes_tokens[0].name
    OAUTH2_RETURN_URL      = local.oauth_callback_url
  }
  k8s_secret_statement = var.kubernetes == null ? [] : [{
    Effect   = "Allow"
    Action   = ["secretsmanager:GetSecretValue"]
    Resource = var.kubernetes.oidc_secret_arn
  }]
  k8s_runtime_statements = var.kubernetes == null ? [] : concat(local.k8s_secret_statement, [{
    Effect   = "Allow"
    Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem"]
    Resource = aws_dynamodb_table.kubernetes_tokens[0].arn
  }])
  k8s_callback_statements = var.kubernetes == null ? [] : concat(local.k8s_secret_statement, [{
    Effect   = "Allow"
    Action   = ["dynamodb:PutItem"]
    Resource = aws_dynamodb_table.kubernetes_tokens[0].arn
  }])
  k8s_confirm_statements = var.kubernetes == null ? [] : [{
    Effect   = "Allow"
    Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem"]
    Resource = aws_dynamodb_table.kubernetes_tokens[0].arn
  }]
}

output "kubernetes_callback_url" {
  description = "Register this exact redirect URI on the lab's dedicated Pinniped OIDCClient."
  value       = local.oauth_callback_url
}

output "slack_interactions_url" {
  description = "Enable Slack Interactivity and use this request URL for identity-confirmation buttons."
  value       = "${local.public_base_url}/slack/interactions"
}
