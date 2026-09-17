data "aws_caller_identity" "current" {}

locals {
  account_id  = data.aws_caller_identity.current.account_id
  name_prefix = "${var.project_name}-${var.env}"
  # AgentCore Runtime names allow only letters, digits and underscores.
  runtime_name = replace(local.name_prefix, "-", "_")

  backends_dir = abspath("${path.root}/../../backends")

  public_base_url    = aws_apigatewayv2_api.this.api_endpoint
  oauth_callback_url = "${local.public_base_url}/oauth2/callback"

  # us.amazon.nova-micro-v1:0 -> amazon.nova-micro-v1:0
  foundation_model_id = replace(var.model_id, "/^(us|eu|apac|global)\\./", "")
  model_resource_arns = [
    "arn:aws:bedrock:${var.region}:${local.account_id}:inference-profile/${var.model_id}",
    # Cross-region inference profiles route to the model in several regions.
    "arn:aws:bedrock:*::foundation-model/${local.foundation_model_id}",
  ]
}
