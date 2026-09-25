data "aws_caller_identity" "current" {}

locals {
  account_id  = data.aws_caller_identity.current.account_id
  name_prefix = "${var.project_name}-${var.env}"
  # AgentCore Runtime names allow only letters, digits and underscores.
  runtime_name = replace(local.name_prefix, "-", "_")

  backends_dir = abspath("${path.root}/../../backends")

  public_base_url    = aws_apigatewayv2_api.this.api_endpoint
  oauth_callback_url = "${local.public_base_url}/oauth2/callback"
  # CIMD: the client metadata document's URL *is* our OAuth client_id. It must be the
  # URL the document is actually served from -- authorization servers check that the
  # `client_id` inside the document matches where they fetched it.
  cimd_client_id = "${local.public_base_url}/oauth2/client-metadata.json"

  # The chat model and the MCP sub-agents' models may differ (e.g. Nova Micro vs Claude
  # Haiku), so all of them need to be allow-listed for the runtime's InvokeModel permission.
  bedrock_model_ids = distinct([var.model_id, var.github_model_id, var.cimd_model_id])
  model_arns_by_id = {
    for model_id in distinct(concat(local.bedrock_model_ids, [var.triage_model_id])) : model_id => [
      "arn:aws:bedrock:${var.region}:${local.account_id}:inference-profile/${model_id}",
      # Cross-region inference profiles route to the model in several regions.
      # us.amazon.nova-micro-v1:0 -> amazon.nova-micro-v1:0
      "arn:aws:bedrock:*::foundation-model/${replace(model_id, "/^(us|eu|apac|global)\\./", "")}",
    ]
  }
  model_resource_arns = flatten([for model_id in local.bedrock_model_ids : local.model_arns_by_id[model_id]])
  # The agent-worker Lambda's triage call; the runtime never needs this model.
  triage_model_resource_arns = local.model_arns_by_id[var.triage_model_id]
}
