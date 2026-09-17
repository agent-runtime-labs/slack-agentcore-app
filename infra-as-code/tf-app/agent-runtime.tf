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
  description        = "Slack assistant with per-user LinkedIn access"
  image_uri          = module.agent_image.image_uri
  ecr_repository_arn = module.agent_image.repository_arn

  model_resource_arns              = local.model_resource_arns
  oauth2_credential_provider_names = [var.linkedin_provider_name, var.github_provider_name]
  allowed_oauth2_return_urls       = [local.oauth_callback_url]
  idle_session_timeout_seconds     = 300
  max_session_lifetime_seconds     = 3600

  environment_variables = {
    LOG_LEVEL              = var.log_level
    MODEL_ID               = var.model_id
    GITHUB_MODEL_ID        = var.github_model_id
    LINKEDIN_PROVIDER_NAME = var.linkedin_provider_name
    GITHUB_PROVIDER_NAME   = var.github_provider_name
    OAUTH2_RETURN_URL      = local.oauth_callback_url
  }
}
