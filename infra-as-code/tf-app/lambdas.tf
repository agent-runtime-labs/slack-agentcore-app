################################################################################
# Lambda functions (one image, three handlers)
################################################################################

module "lambda_image" {
  source = "../tf-modules/aws/container-image"

  repository_name = "${local.name_prefix}-lambdas"
  region          = var.region
  source_dir      = "${local.backends_dir}/lambdas"
  target          = "lambda"
  image_tag       = var.lambda_image_tag
  skip_build      = var.lambda_image_tag != ""
}

locals {
  lambda_common_env = {
    LOG_LEVEL        = var.log_level
    SLACK_SECRET_ARN = aws_secretsmanager_secret.slack.arn
  }

  read_slack_secret = {
    Effect   = "Allow"
    Action   = ["secretsmanager:GetSecretValue"]
    Resource = aws_secretsmanager_secret.slack.arn
  }
}

# POST /slack/events: verify, acknowledge within 3s, enqueue.
module "slack_events_fn" {
  source = "../tf-modules/aws/lambda-function"

  name        = "${local.name_prefix}-slack-events"
  description = "Verifies Slack events and queues them for the agent"
  image_uri   = module.lambda_image.image_uri
  command     = ["slack_app.handlers.slack_events.handler"]
  timeout     = 10

  environment_variables = merge(local.lambda_common_env, {
    PROCESSING_QUEUE_URL = aws_sqs_queue.processing.url
  })

  policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      local.read_slack_secret,
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.processing.arn
      },
    ]
  })
}

# SQS consumer: invoke AgentCore Runtime as the Slack user, reply in Slack.
module "agent_worker_fn" {
  source = "../tf-modules/aws/lambda-function"

  name        = "${local.name_prefix}-agent-worker"
  description = "Invokes the AgentCore Runtime on behalf of the Slack user"
  image_uri   = module.lambda_image.image_uri
  command     = ["slack_app.handlers.agent_worker.handler"]
  timeout     = 150
  memory_size = 512

  environment_variables = merge(local.lambda_common_env, {
    AGENT_RUNTIME_ARN  = module.agent_runtime.agent_runtime_arn
    PENDING_AUTH_TABLE = aws_dynamodb_table.pending_auth.name
    PUBLIC_BASE_URL    = local.public_base_url
  })

  policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      local.read_slack_secret,
      {
        Effect   = "Allow"
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        Resource = aws_sqs_queue.processing.arn
      },
      {
        # InvokeAgentRuntimeForUser lets this role choose the runtimeUserId, i.e.
        # act as any Slack user. Keep it limited to this function and runtime.
        Effect = "Allow"
        Action = ["bedrock-agentcore:InvokeAgentRuntime", "bedrock-agentcore:InvokeAgentRuntimeForUser"]
        Resource = [
          module.agent_runtime.agent_runtime_arn,
          "${module.agent_runtime.agent_runtime_arn}/runtime-endpoint/*",
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem"]
        Resource = aws_dynamodb_table.pending_auth.arn
      },
    ]
  })
}

resource "aws_lambda_event_source_mapping" "agent_worker" {
  event_source_arn        = aws_sqs_queue.processing.arn
  function_name           = module.agent_worker_fn.function_arn
  batch_size              = 1
  function_response_types = ["ReportBatchItemFailures"]
}

# GET /oauth2/start and /oauth2/callback: session binding for user consent.
module "oauth_callback_fn" {
  source = "../tf-modules/aws/lambda-function"

  name        = "${local.name_prefix}-oauth-callback"
  description = "Binds consent to the Slack user, completes the token exchange, and serves the CIMD client document"
  image_uri   = module.lambda_image.image_uri
  command     = ["slack_app.handlers.oauth_callback.handler"]
  timeout     = 15

  environment_variables = merge(local.lambda_common_env, {
    PENDING_AUTH_TABLE = aws_dynamodb_table.pending_auth.name
    COOKIE_SECURE      = "true"
    # The client metadata document must advertise the URL it is served from, so the
    # handler builds both from the public base URL.
    PUBLIC_BASE_URL          = local.public_base_url
    CIMD_TOKEN_TABLE         = aws_dynamodb_table.cimd_tokens.name
    CIMD_CONNECTION_TTL_DAYS = var.cimd_connection_ttl_days
    # A connect started from the App Home tab has no channel/thread to post an ephemeral
    # confirmation into, so oauth_callback.py's _notify() queues the same "app_home" job
    # slack_events.py uses for the tab's initial load, instead of publishing directly --
    # this function is public and unauthenticated, so it deliberately doesn't hold the
    # bedrock-agentcore invoke permission agent_worker needs to do that.
    PROCESSING_QUEUE_URL = aws_sqs_queue.processing.url
  })

  policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      local.read_slack_secret,
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:DeleteItem", "dynamodb:PutItem"]
        Resource = aws_dynamodb_table.pending_auth.arn
      },
      {
        # Write-only: this function mints a CIMD connection, the agent runtime reads
        # and refreshes it. It never needs to read a token back.
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem"]
        Resource = aws_dynamodb_table.cimd_tokens.arn
      },
      {
        Effect = "Allow"
        Action = ["bedrock-agentcore:CompleteResourceTokenAuth"]
        Resource = [
          "arn:aws:bedrock-agentcore:${var.region}:${local.account_id}:token-vault/default",
          "arn:aws:bedrock-agentcore:${var.region}:${local.account_id}:token-vault/default/*",
          "arn:aws:bedrock-agentcore:${var.region}:${local.account_id}:workload-identity-directory/default",
          "arn:aws:bedrock-agentcore:${var.region}:${local.account_id}:workload-identity-directory/default/*",
        ]
      },
      {
        # CompleteResourceTokenAuth runs as this role and reads the credential
        # provider's OAuth2 client secret from the AgentCore-managed secret to
        # finish the token exchange with LinkedIn.
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = "arn:aws:secretsmanager:${var.region}:${local.account_id}:secret:bedrock-agentcore-identity!default/oauth2/*"
      },
      {
        # Queues the "app_home" job that republishes Home after a connect -- see the
        # PROCESSING_QUEUE_URL comment above for why this queues rather than invokes
        # the agent runtime directly.
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.processing.arn
      },
    ]
  })
}
