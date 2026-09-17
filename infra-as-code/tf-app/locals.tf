data "aws_caller_identity" "current" {}

locals {
  account_id  = data.aws_caller_identity.current.account_id
  name_prefix = "${var.project_name}-${var.env}"
  # AgentCore Runtime names allow only letters, digits and underscores.
  runtime_name = replace(local.name_prefix, "-", "_")

  backends_dir = abspath("${path.root}/../../backends")

  public_base_url    = aws_apigatewayv2_api.this.api_endpoint
  oauth_callback_url = "${local.public_base_url}/oauth2/callback"

  # The chat model and the GitHub MCP sub-agent's model may differ (e.g. Nova Micro vs Claude
  # Haiku), so both need to be allow-listed for the runtime's InvokeModel permission.
  bedrock_model_ids = [var.model_id, var.github_model_id]
  model_resource_arns = flatten([
    for model_id in local.bedrock_model_ids : [
      "arn:aws:bedrock:${var.region}:${local.account_id}:inference-profile/${model_id}",
      # Cross-region inference profiles route to the model in several regions.
      # us.amazon.nova-micro-v1:0 -> amazon.nova-micro-v1:0
      "arn:aws:bedrock:*::foundation-model/${replace(model_id, "/^(us|eu|apac|global)\\./", "")}",
    ]
  ])
}
