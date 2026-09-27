################################################################################
# Agent container image + AgentCore Runtime
################################################################################

module "agent_image" {
  source = "../tf-modules/aws/container-image"

  repository_name = "${local.name_prefix}-agent"
  region          = var.region
  source_dir      = "${local.backends_dir}/agents/slack_agent"
  target          = "dist"
  image_tag       = var.agent_image_tag
  skip_build      = var.agent_image_tag != ""
}

module "agent_runtime" {
  source = "../tf-modules/aws/agentcore-runtime"

  name               = local.runtime_name
  description        = "Slack assistant with per-user LinkedIn, GitHub and CIMD MCP access"
  image_uri          = module.agent_image.image_uri
  ecr_repository_arn = module.agent_image.repository_arn

  model_resource_arns              = local.model_resource_arns
  oauth2_credential_provider_names = [var.linkedin_provider_name, var.github_provider_name]
  allowed_oauth2_return_urls       = [local.oauth_callback_url]
  idle_session_timeout_seconds     = 300
  max_session_lifetime_seconds     = 3600

  # CIMD providers keep their tokens in our own table, so the runtime needs access to it.
  additional_policy_statements = [
    {
      Sid    = "CimdTokenVault"
      Effect = "Allow"
      # GetItem to use a connection, PutItem to save a refreshed token, DeleteItem to
      # forget one the provider has revoked.
      Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem"]
      Resource = aws_dynamodb_table.cimd_tokens.arn
    },
    merge({ Sid = "ReadSlackBotToken" }, local.read_slack_secret),
  ]

  environment_variables = {
    LOG_LEVEL              = var.log_level
    MODEL_ID               = var.model_id
    GITHUB_MODEL_ID        = var.github_model_id
    LINKEDIN_PROVIDER_NAME = var.linkedin_provider_name
    GITHUB_PROVIDER_NAME   = var.github_provider_name
    OAUTH2_RETURN_URL      = local.oauth_callback_url
    # The agent's bot token: it posts live per-tool progress to the Slack placeholder
    # (slack_progress.py) and downloads the files people attach (slack_files.py).
    SLACK_SECRET_ARN = aws_secretsmanager_secret.slack.arn

    # CIMD remote MCP servers: no client ID or secret, just the providers to switch on
    # and the URL that identifies this app to their authorization servers.
    CIMD_PROVIDERS           = join(",", var.cimd_providers)
    CIMD_CLIENT_ID           = local.cimd_client_id
    CIMD_TOKEN_TABLE         = aws_dynamodb_table.cimd_tokens.name
    CIMD_MODEL_ID            = var.cimd_model_id
    CIMD_CONNECTION_TTL_DAYS = var.cimd_connection_ttl_days
  }
}
